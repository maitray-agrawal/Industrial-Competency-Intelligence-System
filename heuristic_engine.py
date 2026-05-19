"""
heuristic_engine.py
--------------------
Multidirectional Knowledge Mapping Engine for IIK-CME.

Maps theory (Topics/Subtopics) to industrial entities (Skills/Operations)
using three strategies, in order of precision:

  1. Exact keyword match  — skill_part field is shared between both datasets.
                            Direct join on normalized skill_part text.
  2. TF-IDF cosine sim   — subtopic.title + matched_operation vs
                            operation.name + operation_summary
  3. Weighted rule score — bonus for matching station codes / shop names
                            appearing in topic content.

All scores are upserted into:
  - skill_operation_map   (skill ↔ operation)
  - topic_skill_map       (topic ↔ skill)
  - competency_map        (station ↔ skill ↔ topic coverage)
"""

from __future__ import annotations

import re
from typing import Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from logger import get_logger
from database import SessionLocal
from taxonomy import TaxonomyNormalizer, STOPWORDS
from models import (
    Station, Process, Operation, Skill, Topic, Subtopic,
    SkillOperationMap, TopicSkillMap, CompetencyMap,
)

logger = get_logger("HeuristicEngine")

_MIN_TFIDF_SCORE = 0.05   # Discard very weak cosine similarities
_KEYWORD_OVERLAP_BONUS = 0.2   # Added to base score when keywords overlap


def _corpus(texts: list[Optional[str]]) -> str:
    """Join and normalize a list of text fragments into a single corpus string."""
    combined = " ".join(t for t in texts if t)
    return TaxonomyNormalizer.normalize(combined)


def _keyword_set(text: Optional[str]) -> set[str]:
    """Return a set of meaningful keywords from a normalized text."""
    if not text:
        return set()
    tokens = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


class KnowledgeMapper:
    """
    Runs the full multi-strategy mapping pipeline.
    Call `run()` after a fresh data ingestion.
    """

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(stop_words="english", min_df=1)

    # ------------------------------------------------------------------
    # PUBLIC ENTRYPOINT
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Execute all mapping stages and return a stats summary."""
        logger.info("KnowledgeMapper.run() started.")
        stats: dict = {}

        with SessionLocal() as session:
            stats["skill_operation"] = self._map_skill_to_operation(session)
            stats["topic_skill"]     = self._map_topic_to_skill(session)
            stats["competency"]      = self._build_competency_map(session)
            session.commit()

        logger.info(f"KnowledgeMapper complete — {stats}")
        return stats

    # ------------------------------------------------------------------
    # STAGE 1 — Skill ↔ Operation  (keyword match + TF-IDF)
    # ------------------------------------------------------------------

    def _map_skill_to_operation(self, session) -> int:
        logger.info("Stage 1: Mapping Skills ↔ Operations.")
        skills     = session.query(Skill).all()
        operations = session.query(Operation).all()

        if not skills or not operations:
            logger.warning("No skills or operations to map.")
            return 0

        # --- Strategy A: Exact skill_part match ---
        mapped = 0
        for skill in skills:
            if not skill.skill_part:
                continue
            for op in operations:
                if op.skill_part and TaxonomyNormalizer.is_duplicate(skill.skill_part, op.skill_part):
                    mapped += self._upsert_skill_op(session, skill.id, op.id, 1.0, "exact")

        # --- Strategy B: TF-IDF cosine similarity ---
        skill_texts = [_corpus([s.name, s.skill_part]) for s in skills]
        op_texts    = [_corpus([o.name, o.operation_summary, o.skill_part]) for o in operations]

        all_texts = skill_texts + op_texts
        if len(set(all_texts)) < 2:
            return mapped  # Not enough vocabulary

        try:
            self.vectorizer.fit(all_texts)
            s_vecs = self.vectorizer.transform(skill_texts)
            o_vecs = self.vectorizer.transform(op_texts)
            sim_matrix = cosine_similarity(s_vecs, o_vecs)

            for i, skill in enumerate(skills):
                for j, op in enumerate(operations):
                    score = float(sim_matrix[i][j])
                    if score >= _MIN_TFIDF_SCORE:
                        # Boost if keyword sets overlap
                        kw_boost = _KEYWORD_OVERLAP_BONUS if (
                            _keyword_set(skill.name) & _keyword_set(op.name)
                        ) else 0.0
                        final_score = min(score + kw_boost, 1.0)
                        mapped += self._upsert_skill_op(session, skill.id, op.id, final_score, "tfidf")
        except Exception as e:
            logger.warning(f"TF-IDF skill-op mapping failed: {e}")

        session.flush()
        return mapped

    # ------------------------------------------------------------------
    # STAGE 2 — Topic ↔ Skill  (skill_part keyword + TF-IDF)
    # ------------------------------------------------------------------

    def _map_topic_to_skill(self, session) -> int:
        logger.info("Stage 2: Mapping Topics ↔ Skills.")
        topics = session.query(Topic).all()
        skills = session.query(Skill).all()

        if not topics or not skills:
            logger.warning("No topics or skills to map.")
            return 0

        mapped = 0

        # --- Strategy A: subtopic.skill_part matches skill.skill_part ---
        subtopics = session.query(Subtopic).all()
        for st in subtopics:
            if not st.skill_part:
                continue
            for skill in skills:
                if skill.skill_part and TaxonomyNormalizer.is_duplicate(st.skill_part, skill.skill_part):
                    mapped += self._upsert_topic_skill(session, st.topic_id, skill.id, 1.0, "exact")

        # --- Strategy B: TF-IDF — subtopic matched_operation vs operation corpus ---
        topic_texts = []
        for t in topics:
            parts = [t.title]
            for st in t.subtopics:
                parts.append(st.title)
                parts.append(st.matched_operation or "")
            topic_texts.append(_corpus(parts))

        skill_texts = [_corpus([s.name, s.skill_part]) for s in skills]
        all_texts = topic_texts + skill_texts

        if len(set(filter(None, all_texts))) < 2:
            return mapped

        try:
            vec2 = TfidfVectorizer(stop_words="english", min_df=1)
            vec2.fit(all_texts)
            t_vecs = vec2.transform(topic_texts)
            s_vecs = vec2.transform(skill_texts)
            sim_matrix = cosine_similarity(t_vecs, s_vecs)

            for i, topic in enumerate(topics):
                for j, skill in enumerate(skills):
                    score = float(sim_matrix[i][j])
                    if score >= _MIN_TFIDF_SCORE:
                        mapped += self._upsert_topic_skill(session, topic.id, skill.id, score, "tfidf")
        except Exception as e:
            logger.warning(f"TF-IDF topic-skill mapping failed: {e}")

        session.flush()
        return mapped

    # ------------------------------------------------------------------
    # STAGE 3 — Competency Map  (Station ↔ Skill ↔ Topic coverage)
    # ------------------------------------------------------------------

    def _build_competency_map(self, session) -> int:
        logger.info("Stage 3: Building Competency Map.")
        stations = session.query(Station).all()
        inserted = 0

        for station in stations:
            # Gather all skills reachable from this station's operations
            required_skill_ids: set[int] = set()
            for process in station.processes:
                for op in process.operations:
                    for sk_link in op.skill_links:
                        required_skill_ids.add(sk_link.skill_id)

            for skill_id in required_skill_ids:
                # Find best covering topic
                best_topic_id = None
                best_coverage = 0.0

                skill = session.get(Skill, skill_id)
                if skill:
                    for tlink in skill.topic_links:
                        if tlink.confidence > best_coverage:
                            best_coverage = tlink.confidence
                            best_topic_id = tlink.topic_id

                stmt = sqlite_insert(CompetencyMap).values(
                    station_id=station.id,
                    skill_id=skill_id,
                    topic_id=best_topic_id,
                    coverage=best_coverage,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["station_id", "skill_id"],
                    set_=dict(
                        topic_id=stmt.excluded.topic_id,
                        coverage=stmt.excluded.coverage,
                        last_computed=stmt.excluded.last_computed,
                    ),
                )
                session.execute(stmt)
                inserted += 1

        session.flush()
        return inserted

    # ------------------------------------------------------------------
    # UPSERT HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert_skill_op(session, skill_id: int, op_id: int, score: float, method: str) -> int:
        existing = session.query(SkillOperationMap).filter_by(
            skill_id=skill_id, operation_id=op_id
        ).first()
        if existing:
            if score > existing.confidence:
                existing.confidence = score
                existing.method = method
            return 0
        session.add(SkillOperationMap(
            skill_id=skill_id, operation_id=op_id, confidence=score, method=method
        ))
        session.flush()
        return 1

    @staticmethod
    def _upsert_topic_skill(session, topic_id: int, skill_id: int, score: float, method: str) -> int:
        existing = session.query(TopicSkillMap).filter_by(
            topic_id=topic_id, skill_id=skill_id
        ).first()
        if existing:
            if score > existing.confidence:
                existing.confidence = score
                existing.method = method
            return 0
        session.add(TopicSkillMap(
            topic_id=topic_id, skill_id=skill_id, confidence=score, method=method
        ))
        session.flush()
        return 1

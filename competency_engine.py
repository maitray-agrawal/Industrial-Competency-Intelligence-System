from __future__ import annotations
from logger import get_logger
from database import SessionLocal
from models import Station, Skill, Topic, CompetencyMap, TopicSkillMap

logger = get_logger("CompetencyEngine")


class CompetencyEngine:
    """
    All methods work purely from pre-computed CompetencyMap rows.
    Run heuristic_engine.KnowledgeMapper.run() first to populate those rows.
    """

    @staticmethod
    def score_station_readiness(station_id: int) -> dict:
        """
        Compute theory-coverage readiness score for a given station.

        Returns:
            {
              "station_code":      str,
              "total_skills":      int,
              "covered_skills":    int,
              "readiness_pct":     float,    # 0–100
              "missing_skills":    list[dict],
              "covered_topics":    list[dict],
            }
        """
        with SessionLocal() as session:
            station = session.get(Station, station_id)
            if not station:
                return {"error": f"Station ID {station_id} not found."}

            competency_rows = session.query(CompetencyMap).filter_by(
                station_id=station_id
            ).all()

            if not competency_rows:
                return {
                    "station_code":   station.station_code,
                    "station_name":   station.name,
                    "total_skills":   0,
                    "covered_skills": 0,
                    "readiness_pct":  0.0,
                    "missing_skills": [],
                    "covered_topics": [],
                    "message": "No competency data. Run mapping engine after data ingestion.",
                }

            total     = len(competency_rows)
            covered   = [r for r in competency_rows if r.coverage > 0]
            not_cvrd  = [r for r in competency_rows if r.coverage == 0]

            missing_skills = []
            for row in not_cvrd:
                skill = session.get(Skill, row.skill_id)
                missing_skills.append({
                    "skill_id":   row.skill_id,
                    "skill_name": skill.name if skill else "Unknown",
                    "skill_part": skill.skill_part if skill else "",
                })

            covered_topics = []
            seen_topics: set[int] = set()
            for row in covered:
                if row.topic_id and row.topic_id not in seen_topics:
                    seen_topics.add(row.topic_id)
                    topic = session.get(Topic, row.topic_id)
                    if topic:
                        covered_topics.append({
                            "topic_id":    topic.id,
                            "topic_code":  topic.topic_code,
                            "topic_title": topic.title,
                            "coverage":    round(row.coverage * 100, 1),
                        })

            readiness_pct = round((len(covered) / total) * 100, 1) if total else 0.0

            return {
                "station_code":   station.station_code,
                "station_name":   station.name,
                "total_skills":   total,
                "covered_skills": len(covered),
                "readiness_pct":  readiness_pct,
                "missing_skills": missing_skills,
                "covered_topics": covered_topics,
            }

    @staticmethod
    def get_recommended_modules(missing_skill_ids: list[int]) -> list[dict]:
        """
        Given a list of skill IDs with no theory coverage, return the best
        matching theory topics that could fill the gap.

        Returns list of dicts: [{topic_code, title, confidence, skills_covered}]
        """
        if not missing_skill_ids:
            return []

        with SessionLocal() as session:
            # Find topics that cover any of the missing skills
            links = session.query(TopicSkillMap).filter(
                TopicSkillMap.skill_id.in_(missing_skill_ids)
            ).all()

            # Aggregate by topic
            topic_agg: dict[int, dict] = {}
            for link in links:
                tid = link.topic_id
                if tid not in topic_agg:
                    topic = session.get(Topic, tid)
                    if not topic:
                        continue
                    topic_agg[tid] = {
                        "topic_id":       tid,
                        "topic_code":     topic.topic_code,
                        "title":          topic.title,
                        "confidence_sum": 0.0,
                        "skills_covered": [],
                    }
                skill = session.get(Skill, link.skill_id)
                topic_agg[tid]["confidence_sum"] += link.confidence
                topic_agg[tid]["skills_covered"].append(
                    skill.name if skill else f"Skill#{link.skill_id}"
                )

            # Sort by sum of confidences descending
            ranked = sorted(
                topic_agg.values(),
                key=lambda x: x["confidence_sum"],
                reverse=True,
            )

            # Clean up output
            for item in ranked:
                item["confidence_avg"] = round(
                    item["confidence_sum"] / max(len(item["skills_covered"]), 1), 3
                )
                del item["confidence_sum"]

            return ranked[:10]

    @staticmethod
    def get_station_knowledge_profile(station_id: int) -> dict:
        """
        Full knowledge profile for a station:
          - shop, station metadata
          - processes + operations
          - skills (with theory coverage)
          - tools
          - mapped theory topics
          - readiness score
        """
        with SessionLocal() as session:
            station = session.get(Station, station_id)
            if not station:
                return {"error": f"Station {station_id} not found."}

            processes_data = []
            for proc in station.processes:
                ops_data = []
                for op in proc.operations:
                    skill_names = [sl.skill.name for sl in op.skill_links if sl.skill]
                    ops_data.append({
                        "operation_code":    op.operation_code,
                        "name":              op.name,
                        "operation_summary": op.operation_summary,
                        "skill_part":        op.skill_part,
                        "skills":            skill_names,
                    })
                processes_data.append({
                    "process_code": proc.process_code,
                    "name":         proc.name,
                    "operations":   ops_data,
                })

            tools_data = [
                {"tool_code": tl.tool.tool_code, "name": tl.tool.name}
                for tl in station.tool_links if tl.tool
            ]

            readiness = CompetencyEngine.score_station_readiness(station_id)

            return {
                "station_id":   station.id,
                "station_code": station.station_code,
                "station_name": station.name,
                "shop":         station.shop.name if station.shop else "N/A",
                "processes":    processes_data,
                "tools":        tools_data,
                "readiness":    readiness,
            }

    @staticmethod
    def get_all_stations_summary() -> list[dict]:
        """Return readiness summary for all stations — used by dashboard."""
        with SessionLocal() as session:
            stations = session.query(Station).all()
            summaries = []
            for stn in stations:
                rows = session.query(CompetencyMap).filter_by(station_id=stn.id).all()
                total   = len(rows)
                covered = sum(1 for r in rows if r.coverage > 0)
                pct     = round((covered / total) * 100, 1) if total else 0.0
                summaries.append({
                    "station_id":    stn.id,
                    "station_code":  stn.station_code,
                    "station_name":  stn.name,
                    "shop":          stn.shop.name if stn.shop else "",
                    "total_skills":  total,
                    "covered":       covered,
                    "readiness_pct": pct,
                })
            return sorted(summaries, key=lambda x: x["readiness_pct"], reverse=True)

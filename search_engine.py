"""
search_engine.py
----------------
Hardened FTS5 Search Engine for IIK-CME.

Indexes all industrial entities: Shop, Station, Process, Operation,
Skill, Tool, Topic, Subtopic.

Search pipeline:
  1. Sanitize natural-language query → valid FTS5 AND syntax
  2. Run FTS5 MATCH with BM25 ranking + snippet highlighting
  3. Fall back to LIKE search if FTS5 fails or returns zero results
  4. Return empty list (never raise) so the route never shows a crash page
"""

import re
from sqlalchemy import text
from logger import get_logger
from database import engine, SessionLocal
from models import Shop, Station, Process, Operation, Skill, Tool, Topic, Subtopic

logger = get_logger("SearchEngine")

# Common stopwords — words that break FTS5 or add no search value
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "not", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "it", "its", "as",
    "be", "was", "are", "were", "this", "that", "these", "those",
    "into", "onto", "also", "such", "each", "both", "all", "any",
    "per", "via", "vs", "no", "yes",
})


# =============================================================================
# SEARCH INDEX BUILDER
# =============================================================================

class SearchIndexer:
    """Rebuilds the FTS5 virtual table from all golden-record tables."""

    @staticmethod
    def rebuild_index() -> int:
        logger.info("Starting FTS5 search index rebuild.")
        count = 0

        try:
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM search_index"))

                with SessionLocal() as session:
                    # Shops
                    for obj in session.query(Shop).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "SHOP", "id": obj.id, "title": obj.shop_code,
                            "content": obj.name})
                        count += 1

                    # Stations
                    for obj in session.query(Station).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "STATION", "id": obj.id, "title": obj.station_code,
                            "content": obj.name})
                        count += 1

                    # Processes
                    for obj in session.query(Process).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "PROCESS", "id": obj.id, "title": obj.process_code,
                            "content": obj.name})
                        count += 1

                    # Operations
                    for obj in session.query(Operation).all():
                        content = f"{obj.name} {obj.operation_summary or ''} {obj.skill_part or ''}"
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "OPERATION", "id": obj.id, "title": obj.operation_code,
                            "content": content.strip()})
                        count += 1

                    # Skills
                    for obj in session.query(Skill).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "SKILL", "id": obj.id, "title": obj.skill_code,
                            "content": f"{obj.name} {obj.skill_part or ''}"})
                        count += 1

                    # Tools
                    for obj in session.query(Tool).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "TOOL", "id": obj.id, "title": obj.tool_code,
                            "content": f"{obj.name} {obj.description or ''}"})
                        count += 1

                    # Topics
                    for obj in session.query(Topic).all():
                        subtopic_text = " ".join(
                            f"{st.title} {st.matched_operation or ''}"
                            for st in obj.subtopics
                        )
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "TOPIC", "id": obj.id, "title": obj.topic_code,
                            "content": f"{obj.title} {subtopic_text}".strip()})
                        count += 1

                    # Subtopics
                    for obj in session.query(Subtopic).all():
                        conn.execute(text(
                            "INSERT INTO search_index (entity_type, entity_id, title, content) "
                            "VALUES (:t, :id, :title, :content)"
                        ), {"t": "SUBTOPIC", "id": obj.id, "title": obj.subtopic_code,
                            "content": f"{obj.title} {obj.matched_operation or ''} {obj.skill_part or ''}"})
                        count += 1

                conn.commit()

            logger.info(f"Search index rebuilt — {count} documents indexed.")
            return count

        except Exception as e:
            logger.error(f"Failed to rebuild search index: {e}")
            raise


# =============================================================================
# SEARCH API
# =============================================================================

class SearchAPI:
    """
    Executes natural-language queries against the FTS5 index.

    Handles:
      - Punctuation stripping (commas, quotes, slashes, etc.)
      - Stopword removal
      - AND-joined FTS5 query construction
      - LIKE fallback if FTS5 returns nothing
      - Never raises — returns [] on total failure
    """

    @staticmethod
    def _sanitize_for_fts5(raw_query: str) -> str:
        """
        Convert natural-language text to a valid FTS5 AND query.

        'Glass, roof, and sunroof positioning'
          → 'glass AND roof AND sunroof AND positioning'
        """
        cleaned = re.sub(r"[^\w\s]", " ", raw_query.lower())
        tokens = [
            t for t in cleaned.split()
            if t not in _STOPWORDS and len(t) > 1
        ]
        return " AND ".join(tokens)

    @staticmethod
    def _fallback_like_search(conn, raw_query: str, limit: int) -> list[dict]:
        """LIKE search — safe fallback when FTS5 yields nothing."""
        like_pat = f"%{raw_query.strip()}%"
        result = conn.execute(text("""
            SELECT entity_type, entity_id, title, content AS highlight, 0 AS rank
            FROM search_index
            WHERE title LIKE :pat OR content LIKE :pat
            LIMIT :limit
        """), {"pat": like_pat, "limit": limit})
        return [dict(row._mapping) for row in result]

    @staticmethod
    def search(query_string: str, limit: int = 20,
               entity_filter: str | None = None) -> list[dict]:
        """
        Execute a search against the FTS5 index.

        Args:
            query_string:  Natural-language search text.
            limit:         Maximum number of results.
            entity_filter: Optional entity type filter (e.g. 'STATION', 'TOPIC').
        """
        logger.info(f"Search: '{query_string}' filter={entity_filter}")

        if not query_string or not query_string.strip():
            return []

        fts5_query = SearchAPI._sanitize_for_fts5(query_string)
        logger.debug(f"FTS5 query: '{fts5_query}'")

        try:
            with engine.connect() as conn:
                results: list[dict] = []

                # Build optional entity-type filter clause
                type_clause = ""
                params: dict = {"q": fts5_query, "limit": limit}
                if entity_filter:
                    type_clause = "AND entity_type = :etype"
                    params["etype"] = entity_filter.upper()

                # --- Primary: FTS5 MATCH ---
                if fts5_query:
                    try:
                        result = conn.execute(text(f"""
                            SELECT
                                entity_type,
                                entity_id,
                                title,
                                snippet(search_index, 3, '<b>', '</b>', '...', 15) AS highlight,
                                rank
                            FROM search_index
                            WHERE search_index MATCH :q {type_clause}
                            ORDER BY rank
                            LIMIT :limit
                        """), params)
                        results = [dict(row._mapping) for row in result]
                    except Exception as fts_err:
                        logger.warning(f"FTS5 failed ('{fts5_query}'): {fts_err}. Using LIKE fallback.")

                # --- Fallback: LIKE ---
                if not results:
                    logger.info("FTS5 empty — using LIKE fallback.")
                    results = SearchAPI._fallback_like_search(conn, query_string, limit)

                logger.info(f"Found {len(results)} results.")
                return results

        except Exception as e:
            logger.error(f"Search failed entirely: {e}")
            return []

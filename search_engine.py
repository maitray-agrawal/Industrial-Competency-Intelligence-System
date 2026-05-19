from sqlalchemy import text
from logger import get_logger
from database import engine, SessionLocal
from models import Workstation, Tool, Skill, AcademicTheory

logger = get_logger("SearchEngine")

class SearchIndexer:
    """Rebuilds the FTS5 search index via a deterministic offline batch job."""
    
    @staticmethod
    def rebuild_index():
        logger.info("Starting FTS5 search index rebuild.")
        
        try:
            with engine.connect() as conn:
                # Clear existing index completely
                conn.execute(text("DELETE FROM search_index"))
                
                with SessionLocal() as session:
                    # 1. Index Workstations
                    workstations = session.query(Workstation).all()
                    for ws in workstations:
                        conn.execute(text("""
                            INSERT INTO search_index (entity_type, entity_id, title, content)
                            VALUES (:type, :id, :title, :content)
                        """), {
                            "type": "WORKSTATION",
                            "id": ws.id,
                            "title": ws.workstation_code,
                            "content": ws.description or ""
                        })
                        
                    # 2. Index Tools
                    tools = session.query(Tool).all()
                    for t in tools:
                        conn.execute(text("""
                            INSERT INTO search_index (entity_type, entity_id, title, content)
                            VALUES (:type, :id, :title, :content)
                        """), {
                            "type": "TOOL",
                            "id": t.id,
                            "title": t.tool_name,
                            "content": t.description or ""
                        })
                        
                    # 3. Index Skills
                    skills = session.query(Skill).all()
                    for s in skills:
                        conn.execute(text("""
                            INSERT INTO search_index (entity_type, entity_id, title, content)
                            VALUES (:type, :id, :title, :content)
                        """), {
                            "type": "SKILL",
                            "id": s.id,
                            "title": s.name,
                            "content": f"{s.skill_code} {s.description or ''}"
                        })
                        
                    # 4. Index Theories
                    theories = session.query(AcademicTheory).all()
                    for th in theories:
                        conn.execute(text("""
                            INSERT INTO search_index (entity_type, entity_id, title, content)
                            VALUES (:type, :id, :title, :content)
                        """), {
                            "type": "THEORY",
                            "id": th.id,
                            "title": th.title,
                            "content": f"{th.module_code} {th.content or ''}"
                        })
                        
                conn.commit()
            logger.info("Search index rebuilt successfully.")
        except Exception as e:
            logger.error(f"Failed to rebuild search index: {str(e)}")
            raise

class SearchAPI:
    """Executes sub-millisecond full-text queries against the FTS5 index using BM25 ranking."""
    
    @staticmethod
    def search(query_string: str, limit: int = 10):
        logger.info(f"Executing search for query: '{query_string}'")
        
        try:
            with engine.connect() as conn:
                # SQLite FTS5 handles the MATCH operator and provides snippet highlighting and ranking (lower rank score = better)
                result = conn.execute(text("""
                    SELECT 
                        entity_type, 
                        entity_id, 
                        title, 
                        snippet(search_index, 3, '<b>', '</b>', '...', 15) as highlight,
                        rank
                    FROM search_index
                    WHERE search_index MATCH :q
                    ORDER BY rank
                    LIMIT :limit
                """), {"q": query_string, "limit": limit})
                
                results = [dict(row._mapping) for row in result]
                
            logger.info(f"Found {len(results)} results for query: '{query_string}'")
            return results
        except Exception as e:
            logger.error(f"Search query failed: {str(e)}")
            raise

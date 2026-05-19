from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.dialects.sqlite import insert
from logger import get_logger
from database import SessionLocal
from models import Trade, Workstation, MappingEngine

logger = get_logger("HeuristicEngine")

class MappingCalculator:
    """Calculates TF-IDF cosine similarity between Trades and Workstations."""
    
    def __init__(self):
        # We use english stop words to remove common words that don't add value
        self.vectorizer = TfidfVectorizer(stop_words='english')

    def compute_all_mappings(self):
        logger.info("Starting heuristic mapping calculation.")
        with SessionLocal() as session:
            try:
                trades = session.query(Trade).all()
                workstations = session.query(Workstation).all()
                
                if not trades or not workstations:
                    logger.warning("No trades or workstations found to map.")
                    return
                
                trade_corpuses = []
                trade_ids = []
                for t in trades:
                    # Construct Trade corpus
                    corpus = f"{t.name} {t.description or ''}"
                    trade_corpuses.append(corpus)
                    trade_ids.append(t.id)
                    
                ws_corpuses = []
                ws_ids = []
                for ws in workstations:
                    # Build rich workstation corpus including nested skills and theories
                    ws_text_parts = [ws.workstation_code, ws.description or ""]
                    
                    for skill in ws.skills:
                        ws_text_parts.append(skill.name)
                        ws_text_parts.append(skill.description or "")
                        
                        for theory in skill.academic_theories:
                            ws_text_parts.append(theory.title)
                            ws_text_parts.append(theory.content or "")
                            
                    corpus = " ".join(ws_text_parts)
                    ws_corpuses.append(corpus)
                    ws_ids.append(ws.id)
                
                # Fit the vectorizer on the combined vocabulary of trades and workstations
                all_texts = trade_corpuses + ws_corpuses
                self.vectorizer.fit(all_texts)
                
                trade_vectors = self.vectorizer.transform(trade_corpuses)
                ws_vectors = self.vectorizer.transform(ws_corpuses)
                
                # Computes cosine similarity yielding a matrix of dimensions (n_trades, n_workstations)
                similarity_matrix = cosine_similarity(trade_vectors, ws_vectors)
                
                # Upsert into MappingEngine table
                for i, t_id in enumerate(trade_ids):
                    for j, w_id in enumerate(ws_ids):
                        score = float(similarity_matrix[i][j])
                        
                        stmt = insert(MappingEngine).values(
                            trade_id=t_id,
                            workstation_id=w_id,
                            relevance_score=score
                        )
                        
                        # SQLite specific UPSERT using ON CONFLICT DO UPDATE
                        stmt = stmt.on_conflict_do_update(
                            index_elements=['trade_id', 'workstation_id'],
                            set_=dict(
                                relevance_score=stmt.excluded.relevance_score,
                                last_computed=stmt.excluded.last_computed
                            )
                        )
                        
                        session.execute(stmt)
                
                session.commit()
                logger.info(f"Successfully computed and stored mappings for {len(trades)} trades and {len(workstations)} workstations.")
            except Exception as e:
                session.rollback()
                logger.error(f"Error during heuristic mapping: {str(e)}")
                raise

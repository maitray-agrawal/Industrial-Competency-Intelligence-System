import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.engine import Engine
from logger import get_logger

logger = get_logger("DatabaseManager")

# Establish deterministic local path for SQLite DB
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "industrial_knowledge.sqlite")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Create the Engine
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} # Required for SQLite if sharing across threads
)

# Enforce SQLite PRAGMAs for performance (WAL) and integrity (Foreign Keys)
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception as e:
        logger.error(f"Failed to apply SQLite PRAGMAs: {str(e)}")
        raise

# Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative Base for models
Base = declarative_base()

def get_db_session():
    """
    Provides a transactional scope around a series of operations.
    Useful as a context manager.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception as e:
        logger.error(f"Database session error: {str(e)}")
        session.rollback()
        raise
    finally:
        session.close()

def init_db():
    """
    Initializes the database schema.
    """
    try:
        logger.info("Initializing database schema...")
        Base.metadata.create_all(bind=engine)
        
        # Initialize FTS5 Virtual Table
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    entity_type,
                    entity_id UNINDEXED,
                    title,
                    content
                )
            '''))
            conn.commit()
            
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to initialize database schema: {str(e)}")
        raise

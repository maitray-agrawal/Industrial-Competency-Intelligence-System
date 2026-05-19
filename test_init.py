from database import init_db
from data_engine import logger

if __name__ == "__main__":
    try:
        init_db()
        logger.info("Database initialization test successful.")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")

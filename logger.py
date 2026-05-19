import logging
import sys
import os

def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger that writes to both console and a local system.log file.
    Ensures that logs are properly formatted for auditing in an air-gapped environment.
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Log format: [TIMESTAMP] [LEVEL] [MODULE] - MESSAGE
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(name)s] - %(message)s'
        )
        
        # File handler (Local system.log)
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system.log')
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        
        # Stream handler (Console)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.INFO)
        
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        
    return logger

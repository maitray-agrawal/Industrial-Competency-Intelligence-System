import os
import uuid
import json
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from logger import get_logger
from database import SessionLocal
from models import StagingData, Trade, Student, Workstation, Skill, AcademicTheory

logger = get_logger("DataEngine")

class IngestionPipeline:
    """Reads raw CSV files and inserts them into the StagingData table."""
    
    @staticmethod
    def ingest_csv(file_path: str, source_type: str) -> str:
        batch_id = str(uuid.uuid4())
        logger.info(f"Starting ingestion for {file_path}. Batch ID: {batch_id}")
        
        try:
            # Read CSV using pandas
            df = pd.read_csv(file_path)
            
            # Convert NaN to None for proper JSON serialization
            df = df.where(pd.notnull(df), None)
            records = df.to_dict(orient="records")
            
            with SessionLocal() as session:
                for row in records:
                    staging_record = StagingData(
                        batch_id=batch_id,
                        source_file=source_type,
                        raw_data=row,
                        status="PENDING"
                    )
                    session.add(staging_record)
                
                session.commit()
            
            logger.info(f"Successfully ingested {len(records)} records from {file_path}.")
            return batch_id
            
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Error during ingestion of {file_path}: {str(e)}")
            raise

class ValidationEngine:
    """Validates pending staging records based on expected schema."""
    
    EXPECTED_SCHEMAS = {
        "student_data": ["student_code", "first_name", "last_name", "trade_name", "enrollment_date"],
        "trade_data": ["trade_name", "description"],
        "workstation_data": ["workstation_code", "description", "trade_name"],
        "skill_data": ["skill_code", "name", "description", "workstation_code"],
        "theory_data": ["module_code", "title", "content", "skill_code"]
    }

    @staticmethod
    def validate_pending_records():
        logger.info("Starting validation of PENDING records in staging.")
        
        with SessionLocal() as session:
            try:
                pending_records = session.query(StagingData).filter(StagingData.status == "PENDING").all()
                
                for record in pending_records:
                    source_type = record.source_file
                    raw_data = record.raw_data
                    
                    if source_type not in ValidationEngine.EXPECTED_SCHEMAS:
                        record.status = "FAILED"
                        record.error_log = f"Unknown source file type: {source_type}"
                        continue
                    
                    expected_keys = ValidationEngine.EXPECTED_SCHEMAS[source_type]
                    missing_keys = [k for k in expected_keys if k not in raw_data or raw_data[k] is None]
                    
                    if missing_keys:
                        record.status = "FAILED"
                        record.error_log = f"Missing or null required keys: {missing_keys}"
                    else:
                        record.status = "VALIDATED"
                        
                session.commit()
                logger.info(f"Validation complete for {len(pending_records)} records.")
                
            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Database error during validation: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error during validation: {str(e)}")
                raise

class NormalizationEngine:
    """Migrates VALIDATED staging records into the Golden Record tables."""
    
    @staticmethod
    def process_validated_records():
        logger.info("Starting normalization of VALIDATED records into Golden Records.")
        
        with SessionLocal() as session:
            try:
                # We must process in order of dependency to avoid foreign key violations.
                # Trade -> Workstation -> Skill -> Theory -> Student
                
                order_of_processing = [
                    "trade_data",
                    "workstation_data",
                    "skill_data",
                    "theory_data",
                    "student_data"
                ]
                
                for source_type in order_of_processing:
                    records = session.query(StagingData).filter(
                        StagingData.status == "VALIDATED",
                        StagingData.source_file == source_type
                    ).all()
                    
                    if not records:
                        continue
                    
                    logger.info(f"Processing {len(records)} records for {source_type}")
                    
                    for record in records:
                        try:
                            NormalizationEngine._process_single_record(session, source_type, record.raw_data)
                            record.status = "PROCESSED"
                        except Exception as e:
                            logger.error(f"Failed to process record {record.id}: {str(e)}")
                            record.status = "FAILED"
                            record.error_log = f"Normalization error: {str(e)}"
                            
                session.commit()
                logger.info("Normalization complete.")
                
            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Database error during normalization: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error during normalization: {str(e)}")
                raise

    @staticmethod
    def _process_single_record(session: Session, source_type: str, data: dict):
        """Processes a single dictionary into the appropriate ORM model."""
        
        if source_type == "trade_data":
            trade = session.query(Trade).filter_by(name=data["trade_name"]).first()
            if not trade:
                trade = Trade(name=data["trade_name"], description=data.get("description"))
                session.add(trade)
                session.flush() # Force insert to get ID
                
        elif source_type == "workstation_data":
            trade = session.query(Trade).filter_by(name=data["trade_name"]).first()
            if not trade:
                raise ValueError(f"Trade '{data['trade_name']}' does not exist.")
            
            ws = session.query(Workstation).filter_by(workstation_code=data["workstation_code"]).first()
            if not ws:
                ws = Workstation(
                    workstation_code=data["workstation_code"],
                    description=data.get("description"),
                    trade_id=trade.id
                )
                session.add(ws)
                session.flush()

        elif source_type == "skill_data":
            ws = session.query(Workstation).filter_by(workstation_code=data["workstation_code"]).first()
            if not ws:
                raise ValueError(f"Workstation '{data['workstation_code']}' does not exist.")
                
            skill = session.query(Skill).filter_by(skill_code=data["skill_code"]).first()
            if not skill:
                skill = Skill(
                    skill_code=data["skill_code"],
                    name=data["name"],
                    description=data.get("description"),
                    workstation_id=ws.id
                )
                session.add(skill)
                session.flush()

        elif source_type == "theory_data":
            skill = session.query(Skill).filter_by(skill_code=data["skill_code"]).first()
            if not skill:
                raise ValueError(f"Skill '{data['skill_code']}' does not exist.")
                
            theory = session.query(AcademicTheory).filter_by(module_code=data["module_code"]).first()
            if not theory:
                theory = AcademicTheory(
                    module_code=data["module_code"],
                    title=data["title"],
                    content=data.get("content"),
                    skill_id=skill.id
                )
                session.add(theory)
                session.flush()
                
        elif source_type == "student_data":
            trade = session.query(Trade).filter_by(name=data["trade_name"]).first()
            if not trade:
                raise ValueError(f"Trade '{data['trade_name']}' does not exist.")
                
            student = session.query(Student).filter_by(student_code=data["student_code"]).first()
            if not student:
                # Parse date if necessary, assuming YYYY-MM-DD
                enrollment_date = None
                if data.get("enrollment_date"):
                    enrollment_date = datetime.strptime(data["enrollment_date"], "%Y-%m-%d").date()
                    
                student = Student(
                    student_code=data["student_code"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    trade_id=trade.id,
                    enrollment_date=enrollment_date
                )
                session.add(student)
                session.flush()

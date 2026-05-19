from sqlalchemy import Column, Integer, String, Text, Date, ForeignKey, JSON, DateTime, func, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base

class StagingData(Base):
    __tablename__ = "staging_data"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String, nullable=False, index=True)
    source_file = Column(String, nullable=False)
    raw_data = Column(JSON, nullable=False)
    status = Column(String, default="PENDING") # PENDING, VALIDATED, FAILED, PROCESSED
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(String, nullable=True)

    # Relationships
    students = relationship("Student", back_populates="trade")
    workstations = relationship("Workstation", back_populates="trade")

class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    student_code = Column(String, unique=True, nullable=False, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="RESTRICT"), nullable=False)
    enrollment_date = Column(Date, nullable=True)

    # Relationships
    trade = relationship("Trade", back_populates="students")

class Workstation(Base):
    __tablename__ = "workstations"

    id = Column(Integer, primary_key=True, index=True)
    workstation_code = Column(String, unique=True, nullable=False, index=True)
    description = Column(String, nullable=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    trade = relationship("Trade", back_populates="workstations")
    skills = relationship("Skill", back_populates="workstation", cascade="all, delete-orphan")
    tools = relationship("Tool", back_populates="workstation", cascade="all, delete-orphan")

class Tool(Base):
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True, index=True)
    tool_name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    workstation_id = Column(Integer, ForeignKey("workstations.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    workstation = relationship("Workstation", back_populates="tools")

class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, index=True)
    skill_code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    workstation_id = Column(Integer, ForeignKey("workstations.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    workstation = relationship("Workstation", back_populates="skills")
    academic_theories = relationship("AcademicTheory", back_populates="skill", cascade="all, delete-orphan")

class AcademicTheory(Base):
    __tablename__ = "academic_theories"

    id = Column(Integer, primary_key=True, index=True)
    module_code = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    skill = relationship("Skill", back_populates="academic_theories")

class MappingEngine(Base):
    __tablename__ = "mapping_engine"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    workstation_id = Column(Integer, ForeignKey("workstations.id", ondelete="CASCADE"), nullable=False)
    relevance_score = Column(Float, nullable=False)
    last_computed = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('trade_id', 'workstation_id', name='uq_trade_workstation'),
    )

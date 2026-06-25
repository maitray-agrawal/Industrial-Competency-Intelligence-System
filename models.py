"""
models.py
---------
SQLAlchemy ORM Models for the IIK-CME (Offline Industrial Knowledge &
Competency Mapping Engine).

Schema Design:
  - Core industrial entities from Shop Data uploads:
      Shop → Station → Process → Operation ← Skill, Tool
  - Theory entities from TCF_1.xlsx:
      Diploma → Semester → Topic → Subtopic
  - Junction / mapping tables (M:M relationships + confidence scoring)
  - Staging table (ETL audit trail — unchanged from v1)

All entities carry audit timestamps and use RESTRICT/CASCADE FK rules
appropriate for immutable industrial records.
"""

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, UniqueConstraint, Index, func, Boolean
)
from sqlalchemy.orm import relationship
from database import Base


# =============================================================================
# STAGING / ETL AUDIT
# =============================================================================

class StagingData(Base):
    """ETL audit trail — every uploaded row passes through here."""
    __tablename__ = "staging_data"

    id         = Column(Integer, primary_key=True, index=True)
    batch_id   = Column(String(64), nullable=False, index=True)
    source_file = Column(String(128), nullable=False)
    raw_data   = Column(JSON, nullable=False)
    status     = Column(String(16), default="PENDING", nullable=False)  # PENDING | VALIDATED | PROCESSED | FAILED
    error_log  = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_staging_status", "status"),
        Index("ix_staging_batch", "batch_id"),
    )


# =============================================================================
# CORE INDUSTRIAL ENTITIES 
# =============================================================================

class Shop(Base):
    """Manufacturing shop / plant area (e.g. TCF, Body Frame, Paint,Weld)."""
    __tablename__ = "shops"

    id         = Column(Integer, primary_key=True, index=True)
    shop_code  = Column(String(32), unique=True, nullable=False, index=True)
    name       = Column(String(128), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    stations   = relationship("Station", back_populates="shop", cascade="all, delete-orphan")
    wis_documents = relationship("ShopWISDocument", back_populates="shop", cascade="all, delete-orphan")
    wis_workbooks = relationship("ShopWISWorkbook", back_populates="shop", cascade="all, delete-orphan")
    ppe_workbooks = relationship("ShopPPEWorkbook", back_populates="shop", cascade="all, delete-orphan")


class Station(Base):
    """Individual workstation on the production floor."""
    __tablename__ = "stations"

    id             = Column(Integer, primary_key=True, index=True)
    station_code   = Column(String(64), unique=True, nullable=False, index=True)
    raw_station_id = Column(String(128), nullable=True, index=True)   # original Excel value e.g. TCF_2_STN_7
    name           = Column(String(256), nullable=False)               # display name = raw_station_id
    shop_id        = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    cell           = Column(String(64), nullable=True)                 # CELL column from upload schema
    line           = Column(String(64), nullable=True)                 # LINE column from upload schema
    zone_no        = Column(String(64), nullable=True)                 # ZONE NO column from upload schema
    row_order      = Column(Integer, nullable=True)                    # 0-based Excel row index — preserves upload order
    created_at     = Column(DateTime, server_default=func.now())

    # Relationships
    shop            = relationship("Shop", back_populates="stations")
    processes       = relationship("Process", back_populates="station", cascade="all, delete-orphan")
    tool_links      = relationship("ToolStationMap", back_populates="station", cascade="all, delete-orphan")
    skill_links     = relationship("SkillStationMap", back_populates="station", cascade="all, delete-orphan")
    operation_links = relationship("StationOperationMap", back_populates="station", cascade="all, delete-orphan")
    wis_documents   = relationship("StationWISDocument", back_populates="station", cascade="all, delete-orphan")
    wis_sheets      = relationship("StationWISSheet", back_populates="station", cascade="all, delete-orphan")
    ppe_sheets      = relationship("StationPPESheet", back_populates="station", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_station_shop", "shop_id"),
        Index("ix_station_raw_id", "raw_station_id"),
        Index("ix_station_row_order", "shop_id", "row_order"),
    )



class Process(Base):
    """A production process performed at a station (e.g. Glass Fitting, Door Trim Assy)."""
    __tablename__ = "processes"

    id           = Column(Integer, primary_key=True, index=True)
    process_code = Column(String(64), unique=True, nullable=False, index=True)
    name         = Column(String(256), nullable=False)
    station_id   = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    created_at   = Column(DateTime, server_default=func.now())

    # Relationships
    station    = relationship("Station", back_populates="processes")
    operations = relationship("Operation", back_populates="process", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_process_station", "station_id"),
    )


class Operation(Base):
    """An atomic operation within a process (e.g. 'Torque front door hinge bolts')."""
    __tablename__ = "operations"

    id                = Column(Integer, primary_key=True, index=True)
    operation_code    = Column(String(64), unique=True, nullable=False, index=True)
    name              = Column(String(256), nullable=False)
    operation_summary = Column(Text, nullable=True)
    skill_part        = Column(String(256), nullable=True, index=True)  # normalized shared key with TCF
    process_id        = Column(Integer, ForeignKey("processes.id", ondelete="CASCADE"), nullable=False)
    created_at        = Column(DateTime, server_default=func.now())

    # Relationships
    process      = relationship("Process", back_populates="operations")
    skill_links  = relationship("SkillOperationMap", back_populates="operation", cascade="all, delete-orphan")
    station_links = relationship("StationOperationMap", back_populates="operation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_operation_process", "process_id"),
        Index("ix_operation_skill_part", "skill_part"),
    )


class Skill(Base):
    """An industrial competency / skill (shared between station data and TCF theory)."""
    __tablename__ = "skills"

    id         = Column(Integer, primary_key=True, index=True)
    skill_code = Column(String(64), unique=True, nullable=False, index=True)
    name       = Column(String(256), nullable=False)
    skill_part = Column(String(256), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    operation_links  = relationship("SkillOperationMap", back_populates="skill", cascade="all, delete-orphan")
    topic_links      = relationship("TopicSkillMap", back_populates="skill", cascade="all, delete-orphan")
    competency_links = relationship("CompetencyMap", back_populates="skill", cascade="all, delete-orphan")
    station_links    = relationship("SkillStationMap", back_populates="skill", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_skill_part", "skill_part"),
    )


class Tool(Base):
    """A tool or piece of equipment used at a station."""
    __tablename__ = "tools"

    id          = Column(Integer, primary_key=True, index=True)
    tool_code   = Column(String(64), unique=True, nullable=False, index=True)
    name        = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())

    # Relationships
    station_links = relationship("ToolStationMap", back_populates="tool", cascade="all, delete-orphan")


# =============================================================================
# THEORY ENTITIES  (from TCF_1.xlsx)
# =============================================================================

class Diploma(Base):
    """A trade diploma programme (e.g. Diploma in Automobile Technology)."""
    __tablename__ = "diplomas"

    id         = Column(Integer, primary_key=True, index=True)
    code       = Column(String(32), unique=True, nullable=False, index=True)
    name       = Column(String(256), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    semesters = relationship("Semester", back_populates="diploma", cascade="all, delete-orphan")


class Semester(Base):
    """A semester within a diploma programme."""
    __tablename__ = "semesters"

    id         = Column(Integer, primary_key=True, index=True)
    number     = Column(Integer, nullable=False)
    diploma_id = Column(Integer, ForeignKey("diplomas.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    diploma = relationship("Diploma", back_populates="semesters")
    topics  = relationship("Topic", back_populates="semester", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("number", "diploma_id", name="uq_semester_diploma"),
        Index("ix_semester_diploma", "diploma_id"),
    )


class Topic(Base):
    """A curriculum topic within a semester."""
    __tablename__ = "topics"

    id          = Column(Integer, primary_key=True, index=True)
    topic_code  = Column(String(64), unique=True, nullable=False, index=True)
    title       = Column(String(512), nullable=False)
    semester_id = Column(Integer, ForeignKey("semesters.id", ondelete="CASCADE"), nullable=False)
    created_at  = Column(DateTime, server_default=func.now())

    # Relationships
    semester  = relationship("Semester", back_populates="topics")
    subtopics = relationship("Subtopic", back_populates="topic", cascade="all, delete-orphan")
    skill_links = relationship("TopicSkillMap", back_populates="topic", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_topic_semester", "semester_id"),
    )


class Subtopic(Base):
    """A granular sub-topic that directly maps to a plant operation."""
    __tablename__ = "subtopics"

    id                = Column(Integer, primary_key=True, index=True)
    subtopic_code     = Column(String(64), unique=True, nullable=False, index=True)
    title             = Column(String(512), nullable=False)
    matched_operation = Column(String(512), nullable=True, index=True)  # raw matched_operation field from TCF
    skill_part        = Column(String(256), nullable=True, index=True)  # shared key with Operation
    topic_id          = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    created_at        = Column(DateTime, server_default=func.now())

    # Relationships
    topic = relationship("Topic", back_populates="subtopics")

    __table_args__ = (
        Index("ix_subtopic_topic", "topic_id"),
        Index("ix_subtopic_skill_part", "skill_part"),
    )


# =============================================================================
# JUNCTION / MAPPING TABLES
# =============================================================================

class SkillOperationMap(Base):
    """M:M — Skills ↔ Operations."""
    __tablename__ = "skill_operation_map"

    id           = Column(Integer, primary_key=True, index=True)
    skill_id     = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    operation_id = Column(Integer, ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    confidence   = Column(Float, default=1.0, nullable=False)
    method       = Column(String(32), default="keyword", nullable=False)  # keyword | tfidf | rule
    created_at   = Column(DateTime, server_default=func.now())

    # Relationships
    skill     = relationship("Skill", back_populates="operation_links")
    operation = relationship("Operation", back_populates="skill_links")

    __table_args__ = (
        UniqueConstraint("skill_id", "operation_id", name="uq_skill_operation"),
    )


class ToolStationMap(Base):
    """M:M — Tools ↔ Stations."""
    __tablename__ = "tool_station_map"

    id         = Column(Integer, primary_key=True, index=True)
    tool_id    = Column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    station_id = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    tool    = relationship("Tool", back_populates="station_links")
    station = relationship("Station", back_populates="tool_links")

    __table_args__ = (
        UniqueConstraint("tool_id", "station_id", name="uq_tool_station"),
    )


class SkillStationMap(Base):
    """M:M — Skills ↔ Stations (direct map, bypasses operation/process chain)."""
    __tablename__ = "skill_station_map"

    id         = Column(Integer, primary_key=True, index=True)
    skill_id   = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    station_id = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    confidence = Column(Float, default=1.0, nullable=False)
    method     = Column(String(32), default="etl", nullable=False)  # etl | inferred
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    skill   = relationship("Skill", back_populates="station_links")
    station = relationship("Station", back_populates="skill_links")

    __table_args__ = (
        UniqueConstraint("skill_id", "station_id", name="uq_skill_station"),
        Index("ix_skill_station_station", "station_id"),
    )


class StationOperationMap(Base):
    """M:M — Stations ↔ Operations (direct shortcut link)."""
    __tablename__ = "station_operation_map"

    id           = Column(Integer, primary_key=True, index=True)
    station_id   = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    operation_id = Column(Integer, ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    created_at   = Column(DateTime, server_default=func.now())

    # Relationships
    station   = relationship("Station", back_populates="operation_links")
    operation = relationship("Operation", back_populates="station_links")

    __table_args__ = (
        UniqueConstraint("station_id", "operation_id", name="uq_station_operation"),
        Index("ix_station_operation_station", "station_id"),
    )


class TopicSkillMap(Base):
    """M:M — Topics ↔ Skills (theory-to-competency bridge)."""
    __tablename__ = "topic_skill_map"

    id         = Column(Integer, primary_key=True, index=True)
    topic_id   = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    skill_id   = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    confidence = Column(Float, default=1.0, nullable=False)
    method     = Column(String(32), default="keyword", nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    topic = relationship("Topic", back_populates="skill_links")
    skill = relationship("Skill", back_populates="topic_links")

    __table_args__ = (
        UniqueConstraint("topic_id", "skill_id", name="uq_topic_skill"),
    )


class CompetencyMap(Base):
    """
    Station-level competency record.
    Stores the set of skills required at a station and which theory topics cover them.
    """
    __tablename__ = "competency_map"

    id           = Column(Integer, primary_key=True, index=True)
    station_id   = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    skill_id     = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    topic_id     = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    coverage     = Column(Float, default=0.0, nullable=False)  # 0.0 – 1.0
    last_computed = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    station = relationship("Station")
    skill   = relationship("Skill", back_populates="competency_links")
    topic   = relationship("Topic")

    __table_args__ = (
        UniqueConstraint("station_id", "skill_id", name="uq_competency_station_skill"),
        Index("ix_competency_station", "station_id"),
    )


# =============================================================================
# FILE UPLOAD REGISTRY (Duplicate Prevention)
# =============================================================================

class UploadedFile(Base):
    """Registry of every upload event — one row per ingestion run (not per unique file)."""
    __tablename__ = "uploaded_files"

    id          = Column(Integer, primary_key=True, index=True)
    filename    = Column(String(256), nullable=False)
    file_hash   = Column(String(64), nullable=False, index=True)   # SHA-256; NOT unique — same file can be reprocessed
    shop_code   = Column(String(32), nullable=True, index=True)
    uploaded_by = Column(String(64), nullable=True)
    upload_mode = Column(String(32), nullable=True)                # insert_only | update_only | upsert | reprocess
    status      = Column(String(32), nullable=True, default="ok") # ok | reprocessed | skipped
    upload_time = Column(DateTime, server_default=func.now(), nullable=False)


# =============================================================================
# UNIVERSAL KNOWLEDGE GRAPH LAYERS
# =============================================================================

class GraphEntity(Base):
    """Unified traversable nodes in the Industrial Knowledge Graph."""
    __tablename__ = "entities"

    id          = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)  # station, process, operation, skill, tool, topic, subject, semester, diploma
    code        = Column(String(128), unique=True, nullable=False, index=True)
    name        = Column(String(512), nullable=False)
    properties  = Column(JSON, nullable=True)  # custom metadata (e.g. summaries, shop codes, etc.)
    created_at  = Column(DateTime, server_default=func.now())


class GraphRelationship(Base):
    """Unified weighted connections in the Industrial Knowledge Graph."""
    __tablename__ = "relationships"

    id          = Column(Integer, primary_key=True, index=True)
    source_id   = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    target_id   = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    rel_type    = Column(String(64), nullable=False, index=True)  # requires, uses, mapped_to, depends_on, related_to, studied_in, performed_at
    weight      = Column(Float, default=1.0, nullable=False)
    properties  = Column(JSON, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())

    # Relationships
    source = relationship("GraphEntity", foreign_keys=[source_id])
    target = relationship("GraphEntity", foreign_keys=[target_id])

    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "rel_type", name="uq_graph_relationship"),
        Index("ix_rel_source", "source_id"),
        Index("ix_rel_target", "target_id"),
    )


class EntityType(Base):
    """Registered entity categories."""
    __tablename__ = "entity_types"

    id   = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False)


class MappingScore(Base):
    """Scores computed by TF-IDF / similarity matching."""
    __tablename__ = "mapping_scores"

    id          = Column(Integer, primary_key=True, index=True)
    source_code = Column(String(128), nullable=False, index=True)
    target_code = Column(String(128), nullable=False, index=True)
    score       = Column(Float, nullable=False)
    method      = Column(String(64), nullable=False)  # tfidf, keyword, rules
    created_at  = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_code", "target_code", "method", name="uq_mapping_score"),
    )


class ShopWISDocument(Base):
    """WIS document attached to a manufacturing shop."""
    __tablename__ = "shop_wis_documents"

    id         = Column(Integer, primary_key=True, index=True)
    shop_id    = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    file_name  = Column(String(255), nullable=False)
    file_path  = Column(String(500), nullable=False)
    uploaded_by = Column(String(64), nullable=True)
    uploaded_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    shop = relationship("Shop", back_populates="wis_documents")

    __table_args__ = (
        Index("ix_shop_wis_shop_id", "shop_id"),
    )


class ShopWISWorkbook(Base):
    """Workbook attached to a manufacturing shop and parsed into station-specific sheets."""
    __tablename__ = "shop_wis_workbooks"

    id              = Column(Integer, primary_key=True, index=True)
    shop_id         = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    file_name       = Column(String(255), nullable=False)
    file_path       = Column(String(500), nullable=False)
    sheet_count     = Column(Integer, nullable=False, default=0)
    version_number  = Column(Integer, nullable=False, default=1)
    active          = Column(Boolean, nullable=False, default=True)
    archived_at     = Column(DateTime, nullable=True)
    change_summary  = Column(Text, nullable=True)
    uploaded_by     = Column(String(64), nullable=True)
    uploaded_at     = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    shop = relationship("Shop", back_populates="wis_workbooks")
    sheets = relationship("StationWISSheet", back_populates="workbook", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_shop_wis_workbook_shop_id", "shop_id"),
        Index("ix_shop_wis_workbook_active", "shop_id", "active"),
    )


class StationWISSheet(Base):
    """A single sheet from a shop WIS workbook that maps to a station."""
    __tablename__ = "station_wis_sheets"

    id            = Column(Integer, primary_key=True, index=True)
    workbook_id   = Column(Integer, ForeignKey("shop_wis_workbooks.id", ondelete="CASCADE"), nullable=False)
    station_id    = Column(Integer, ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    sheet_name    = Column(String(255), nullable=False)
    sheet_index   = Column(Integer, nullable=False, default=0)
    match_status  = Column(String(32), nullable=False, default="auto")
    uploaded_at   = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    workbook = relationship("ShopWISWorkbook", back_populates="sheets")
    station = relationship("Station", back_populates="wis_sheets")

    __table_args__ = (
        Index("ix_station_wis_sheet_workbook", "workbook_id"),
        Index("ix_station_wis_sheet_station", "station_id"),
    )


class ShopPPEWorkbook(Base):
    """PPE workbook attached to a manufacturing shop and parsed into station-specific sheets."""
    __tablename__ = "shop_ppe_workbooks"

    id              = Column(Integer, primary_key=True, index=True)
    shop_id         = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    file_name       = Column(String(255), nullable=False)
    file_path       = Column(String(500), nullable=False)
    sheet_count     = Column(Integer, nullable=False, default=0)
    version_number  = Column(Integer, nullable=False, default=1)
    active          = Column(Boolean, nullable=False, default=True)
    archived_at     = Column(DateTime, nullable=True)
    change_summary  = Column(Text, nullable=True)
    uploaded_by     = Column(String(64), nullable=True)
    uploaded_at     = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    shop = relationship("Shop", back_populates="ppe_workbooks")
    sheets = relationship("StationPPESheet", back_populates="workbook", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_shop_ppe_workbook_shop_id", "shop_id"),
        Index("ix_shop_ppe_workbook_active", "shop_id", "active"),
    )


class StationPPESheet(Base):
    """A single sheet from a shop PPE workbook that maps to a station."""
    __tablename__ = "station_ppe_sheets"

    id            = Column(Integer, primary_key=True, index=True)
    workbook_id   = Column(Integer, ForeignKey("shop_ppe_workbooks.id", ondelete="CASCADE"), nullable=False)
    station_id    = Column(Integer, ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    sheet_name    = Column(String(255), nullable=False)
    sheet_index   = Column(Integer, nullable=False, default=0)
    match_status  = Column(String(32), nullable=False, default="auto")
    uploaded_at   = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    workbook = relationship("ShopPPEWorkbook", back_populates="sheets")
    station = relationship("Station", back_populates="ppe_sheets")

    __table_args__ = (
        Index("ix_station_ppe_sheet_workbook", "workbook_id"),
        Index("ix_station_ppe_sheet_station", "station_id"),
    )


class StationWISDocument(Base):
    """WIS document attached to a workstation."""
    __tablename__ = "station_wis_documents"

    id         = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    file_name  = Column(String(255), nullable=False)
    file_path  = Column(String(500), nullable=False)
    uploaded_by = Column(String(64), nullable=True)
    uploaded_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    station = relationship("Station", back_populates="wis_documents")

    __table_args__ = (
        Index("ix_station_wis_station_id", "station_id"),
    )
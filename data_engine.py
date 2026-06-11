import os
import uuid
import hashlib
import pandas as pd
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError
from logger import get_logger
from database import SessionLocal
from taxonomy import TaxonomyNormalizer
from models import (
    StagingData,
    Shop, Station, Process, Operation, Skill, Tool,
    Diploma, Semester, Topic, Subtopic,
    SkillOperationMap, ToolStationMap, SkillStationMap, StationOperationMap,
)

logger = get_logger("DataEngine")

# ---------------------------------------------------------------------------
# Column signature maps — used to auto-detect which dataset was uploaded
# ---------------------------------------------------------------------------
STATION_REQUIRED_COLS = {"shop", "station", "process"}
STATION_OPTIONAL_COLS = {"tools/equipment", "operation summary", "skill part"}

TCF_REQUIRED_COLS = {"topic", "sub-topic"}
TCF_OPTIONAL_COLS = {"diploma", "semester", "matched operation", "skill part"}

# ---------------------------------------------------------------------------
# Column alias map — maps any variant of a column name to its canonical name
# Covers: plural forms, spacing around slashes, all-caps, abbreviated names
# ---------------------------------------------------------------------------
_COLUMN_ALIASES = {
    # Station column aliases
    "stations":          "station",
    "station no":        "station",
    "station number":    "station",
    "station id":        "station",
    "stn":               "station",
    # Shop aliases
    "shop name":         "shop",
    "plant":             "shop",
    "plant / shop":      "shop",
    # Process aliases
    "process name":      "process",
    "process / operation": "process",
    # Tools aliases (many spacing variants around /)
    "tools / equipment": "tools/equipment",
    "tools/equipment":   "tools/equipment",
    "tools & equipment": "tools/equipment",
    "tools and equipment": "tools/equipment",
    "equipment":         "tools/equipment",
    "tools":             "tools/equipment",
    # Operation summary aliases
    "operation summary": "operation summary",
    "operation":         "operation summary",
    "operation desc":    "operation summary",
    "operation description": "operation summary",
    # Skill part aliases
    "skill part":        "skill part",
    "skills":            "skill part",
    "skill":             "skill part",
    "competency":        "skill part",
    # TCF aliases
    "sub-topic":         "sub-topic",
    "subtopic":          "sub-topic",
    "sub topic":         "sub-topic",
    "matched operation":     "matched operation",
    "matched_operation":     "matched operation",
}


def _row_fingerprint(row: dict) -> str:
    """SHA-256 fingerprint of a normalised row dict for duplicate detection."""
    # Safely coerce None → '' so hash is stable and json-serializable
    canonical = str(sorted({k: (str(v) if v is not None else "") for k, v in row.items()}.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lowercase and strip column names, then apply alias map so any variant
    of the column name (e.g. 'STATIONS', 'TOOLS / EQUIPMENT') is resolved
    to the canonical form ('station', 'tools/equipment') the ETL expects.
    """
    df.columns = [
        _COLUMN_ALIASES.get(c.strip().lower(), c.strip().lower())
        for c in df.columns
    ]
    return df


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Replace NaN with None, strip string cells."""
    df = df.where(pd.notnull(df), None)
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].apply(lambda x: x.strip() if isinstance(x, str) else x)
    return df


def detect_dataset_type(df: pd.DataFrame) -> str:
    """
    Infer dataset type from column headers.
    Returns 'station_data', 'tcf_data', or raises ValueError.
    """
    cols = set(df.columns.str.strip().str.lower().tolist())

    if STATION_REQUIRED_COLS.issubset(cols):
        return "station_data"
    if TCF_REQUIRED_COLS.issubset(cols):
        return "tcf_data"

    raise ValueError(
        f"Cannot identify dataset type from columns: {list(cols)}. "
        f"Expected station data columns {STATION_REQUIRED_COLS} or "
        f"TCF columns {TCF_REQUIRED_COLS}."
    )


# =============================================================================
# STATION DATA ETL
# =============================================================================

class StationDataETL:
    """
    Ingests complete_trim_station_data.xlsx.
    Column map: shop | station | process | tools/equipment | operation summary | skill part
    """

    @staticmethod
    def _make_code(*parts: str) -> str:
        """Build a deterministic entity code from normalized name parts."""
        joined = "_".join(TaxonomyNormalizer.normalize_code(p) for p in parts if p)
        return joined[:64]

    @classmethod
    def ingest(cls, df: pd.DataFrame, batch_id: str) -> dict:
        df = _normalize_columns(df)
        df = _clean_df(df)

        stats = {"shops": 0, "stations": 0, "processes": 0,
                 "operations": 0, "skills": 0, "tools": 0,
                 "staged": 0, "skipped": 0, "errors": 0}

        with SessionLocal() as session:
            try:
                seen_fingerprints: set[str] = set()

                for idx, row in df.iterrows():
                    raw = row.to_dict()
                    fp = _row_fingerprint(raw)

                    # Dedup within this batch
                    if fp in seen_fingerprints:
                        stats["skipped"] += 1
                        continue
                    seen_fingerprints.add(fp)

                    # Stage raw row
                    staging = StagingData(
                        batch_id=batch_id,
                        source_file="station_data",
                        raw_data=raw,
                        status="PENDING",
                    )
                    session.add(staging)

                    try:
                        # --- SHOP ---
                        raw_shop = raw.get("shop") or "UNKNOWN_SHOP"
                        raw_shop = str(raw_shop).strip()
                        shop_code = TaxonomyNormalizer.normalize_code(raw_shop)
                        # Title-case display: 'TCF 2' → 'Tcf 2' (not 'trim chassis final 2')
                        shop_name = TaxonomyNormalizer.preserve_industrial_code(raw_shop)
                        shop = session.query(Shop).filter_by(shop_code=shop_code).first()
                        if not shop:
                            shop = Shop(shop_code=shop_code, name=shop_name)
                            session.add(shop)
                            session.flush()
                            stats["shops"] += 1

                        # --- STATION ---
                        raw_stn = raw.get("station")
                        if not raw_stn or not str(raw_stn).strip():
                            logger.warning(f"Row {idx}: blank station value — skipping row.")
                            staging.status = "SKIPPED"
                            stats["skipped"] += 1
                            continue

                        raw_stn = str(raw_stn).strip()
                        # Preserve the original Excel station ID (e.g. TCF_2_STN_7)
                        # station_code = normalize_code(raw_stn)  — unique by itself, no shop prefix
                        station_code = TaxonomyNormalizer.normalize_code(raw_stn)[:64]
                        # Display name = raw value (NOT expanded English text)
                        station_name = TaxonomyNormalizer.preserve_industrial_code(raw_stn)

                        station = session.query(Station).filter_by(station_code=station_code).first()
                        if not station:
                            station = Station(
                                station_code=station_code,
                                raw_station_id=raw_stn,
                                name=station_name,
                                shop_id=shop.id,
                            )
                            session.add(station)
                            session.flush()
                            stats["stations"] += 1

                        # --- PROCESS ---
                        raw_proc = raw.get("process") or f"PROC_{idx}"
                        # Process code: station_code + process name (keeps it unique per station)
                        process_code = cls._make_code(station_code, raw_proc)[:64]
                        # Display name: title-case (not over-normalized English)
                        process_name = TaxonomyNormalizer.title_case(raw_proc)
                        if not process_name:
                            process_name = raw_proc
                        process = session.query(Process).filter_by(process_code=process_code).first()
                        if not process:
                            process = Process(
                                process_code=process_code,
                                name=process_name,
                                station_id=station.id,
                            )
                            session.add(process)
                            session.flush()
                            stats["processes"] += 1

                        # --- OPERATION (operation summary) ---
                        raw_op_summary = raw.get("operation summary") or ""
                        raw_skill_part = raw.get("skill part") or ""
                        op_code = cls._make_code(process_code, raw_op_summary or f"OP_{idx}")
                        operation = session.query(Operation).filter_by(operation_code=op_code).first()
                        if not operation:
                            operation = Operation(
                                operation_code=op_code,
                                name=TaxonomyNormalizer.normalize(raw_op_summary) or process_name,
                                operation_summary=raw_op_summary,
                                skill_part=TaxonomyNormalizer.normalize(raw_skill_part),
                                process_id=process.id,
                            )
                            session.add(operation)
                            session.flush()
                            stats["operations"] += 1

                        # --- SKILL (from skill part column) ---
                        if raw_skill_part:
                            skill_code = cls._make_code("SKILL", raw_skill_part)
                            skill_name = TaxonomyNormalizer.title_case(raw_skill_part)
                            if not skill_name:
                                skill_name = raw_skill_part
                            skill = session.query(Skill).filter_by(skill_code=skill_code).first()
                            if not skill:
                                skill = Skill(
                                    skill_code=skill_code,
                                    name=skill_name,
                                    skill_part=TaxonomyNormalizer.normalize(raw_skill_part),
                                )
                                session.add(skill)
                                session.flush()
                                stats["skills"] += 1

                            # Link skill ↔ operation
                            link = session.query(SkillOperationMap).filter_by(
                                skill_id=skill.id, operation_id=operation.id
                            ).first()
                            if not link:
                                session.add(SkillOperationMap(
                                    skill_id=skill.id,
                                    operation_id=operation.id,
                                    confidence=1.0,
                                    method="keyword",
                                ))

                            # Direct link: skill ↔ station (NEW — bypasses 4-hop chain)
                            slink = session.query(SkillStationMap).filter_by(
                                skill_id=skill.id, station_id=station.id
                            ).first()
                            if not slink:
                                session.add(SkillStationMap(
                                    skill_id=skill.id,
                                    station_id=station.id,
                                    confidence=1.0,
                                    method="etl",
                                ))

                        # --- TOOLS (tools/equipment column — multi-delimiter aware) ---
                        raw_tools = raw.get("tools/equipment") or ""
                        if raw_tools:
                            # Split on ALL common delimiters: , / ; | and newlines
                            tool_names = TaxonomyNormalizer.split_multi_delimited(str(raw_tools))
                            for tool_name_raw in tool_names:
                                if not tool_name_raw:
                                    continue
                                tool_code = TaxonomyNormalizer.normalize_code(
                                    TaxonomyNormalizer.normalize(tool_name_raw)
                                )[:64]
                                if not tool_code:
                                    continue
                                # Title-case display name (not over-normalized)
                                tool_norm = TaxonomyNormalizer.title_case(tool_name_raw)
                                if not tool_norm:
                                    tool_norm = tool_name_raw.strip()
                                tool = session.query(Tool).filter_by(tool_code=tool_code).first()
                                if not tool:
                                    tool = Tool(
                                        tool_code=tool_code,
                                        name=tool_norm,
                                    )
                                    session.add(tool)
                                    session.flush()
                                    stats["tools"] += 1

                                # Link tool ↔ station
                                tlink = session.query(ToolStationMap).filter_by(
                                    tool_id=tool.id, station_id=station.id
                                ).first()
                                if not tlink:
                                    session.add(ToolStationMap(
                                        tool_id=tool.id,
                                        station_id=station.id,
                                    ))

                        # Direct link: station ↔ operation (NEW shortcut)
                        oplink = session.query(StationOperationMap).filter_by(
                            station_id=station.id, operation_id=operation.id
                        ).first()
                        if not oplink:
                            session.add(StationOperationMap(
                                station_id=station.id,
                                operation_id=operation.id,
                            ))

                        staging.status = "PROCESSED"
                        stats["staged"] += 1

                    except Exception as row_err:
                        logger.error(f"Row {idx} failed: {row_err}")
                        staging.status = "FAILED"
                        staging.error_log = str(row_err)
                        stats["errors"] += 1

                session.commit()

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Station ETL database error: {e}")
                raise

        logger.info(f"Station ETL complete — {stats}")
        return stats


# =============================================================================
# TCF THEORY DATA ETL
# =============================================================================

class TCFDataETL:
    """
    Ingests TCF_1.xlsx.
    Column map: diploma | semester | topic | sub-topic | matched operation | skill part
    """

    @staticmethod
    def _make_code(*parts: str) -> str:
        joined = "_".join(TaxonomyNormalizer.normalize_code(p) for p in parts if p)
        return joined[:64]

    @classmethod
    def ingest(cls, df: pd.DataFrame, batch_id: str) -> dict:
        df = _normalize_columns(df)
        df = _clean_df(df)

        stats = {"diplomas": 0, "semesters": 0, "topics": 0,
                 "subtopics": 0, "staged": 0, "skipped": 0, "errors": 0}

        with SessionLocal() as session:
            try:
                seen_fingerprints: set[str] = set()

                for idx, row in df.iterrows():
                    raw = row.to_dict()
                    fp = _row_fingerprint(raw)

                    if fp in seen_fingerprints:
                        stats["skipped"] += 1
                        continue
                    seen_fingerprints.add(fp)

                    staging = StagingData(
                        batch_id=batch_id,
                        source_file="tcf_data",
                        raw_data=raw,
                        status="PENDING",
                    )
                    session.add(staging)

                    try:
                        # --- DIPLOMA ---
                        raw_dip = str(raw.get("diploma") or "DEFAULT_DIPLOMA").strip()
                        dip_code = cls._make_code("DIP", raw_dip)
                        diploma = session.query(Diploma).filter_by(code=dip_code).first()
                        if not diploma:
                            diploma = Diploma(
                                code=dip_code,
                                name=TaxonomyNormalizer.normalize(raw_dip),
                            )
                            session.add(diploma)
                            session.flush()
                            stats["diplomas"] += 1

                        # --- SEMESTER ---
                        raw_sem = raw.get("semester")
                        try:
                            sem_num = int(float(str(raw_sem))) if raw_sem is not None else 1
                        except (ValueError, TypeError):
                            sem_num = 1

                        semester = session.query(Semester).filter_by(
                            number=sem_num, diploma_id=diploma.id
                        ).first()
                        if not semester:
                            semester = Semester(number=sem_num, diploma_id=diploma.id)
                            session.add(semester)
                            session.flush()
                            stats["semesters"] += 1

                        # --- TOPIC ---
                        raw_topic = str(raw.get("topic") or f"TOPIC_{idx}").strip()
                        topic_code = cls._make_code("TOPIC", dip_code, str(sem_num), raw_topic)
                        topic = session.query(Topic).filter_by(topic_code=topic_code).first()
                        if not topic:
                            topic = Topic(
                                topic_code=topic_code,
                                title=TaxonomyNormalizer.normalize(raw_topic),
                                semester_id=semester.id,
                            )
                            session.add(topic)
                            session.flush()
                            stats["topics"] += 1

                        # --- SUBTOPIC ---
                        raw_sub = str(raw.get("sub-topic") or f"SUBTOPIC_{idx}").strip()
                        raw_matched_op = str(raw.get("matched operation") or "").strip()
                        raw_skill_part = str(raw.get("skill part") or "").strip()
                        subtopic_code = cls._make_code("ST", topic_code, raw_sub)
                        subtopic = session.query(Subtopic).filter_by(subtopic_code=subtopic_code).first()
                        if not subtopic:
                            subtopic = Subtopic(
                                subtopic_code=subtopic_code,
                                title=TaxonomyNormalizer.normalize(raw_sub),
                                matched_operation=raw_matched_op,
                                skill_part=TaxonomyNormalizer.normalize(raw_skill_part),
                                topic_id=topic.id,
                            )
                            session.add(subtopic)
                            session.flush()
                            stats["subtopics"] += 1

                        staging.status = "PROCESSED"
                        stats["staged"] += 1

                    except Exception as row_err:
                        logger.error(f"TCF Row {idx} failed: {row_err}")
                        staging.status = "FAILED"
                        staging.error_log = str(row_err)
                        stats["errors"] += 1

                session.commit()

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"TCF ETL database error: {e}")
                raise

        logger.info(f"TCF ETL complete — {stats}")
        return stats


# =============================================================================
# UNIFIED INGESTION ENTRYPOINT
# =============================================================================

class IngestionPipeline:
    """
    Public facade used by app.py.
    Auto-detects dataset type, routes to the appropriate ETL class.
    """

    @staticmethod
    def ingest_excel(file_path: str, source_type: str = "auto") -> dict:
        """
        Read an Excel file and run the appropriate ETL pipeline.

        Args:
            file_path:   Absolute path to the .xlsx / .xls file.
            source_type: 'auto' (default) | 'station_data' | 'tcf_data'

        Returns:
            dict with ingestion stats.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Upload file not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in {".xlsx", ".xls"}:
            raise ValueError(f"Unsupported file format '{ext}'. Only .xlsx and .xls are accepted.")

        try:
            # Use openpyxl explicitly for .xlsx to avoid engine ambiguity
            engine_arg = "openpyxl" if file_path.lower().endswith(".xlsx") else "xlrd"
            df = pd.read_excel(file_path, engine=engine_arg)
        except Exception as e:
            raise ValueError(f"Failed to read Excel file: {e}") from e

        # Drop completely empty rows before any processing
        df = df.dropna(how="all").reset_index(drop=True)

        if df.empty:
            raise ValueError("Uploaded Excel file contains no data rows.")

        # Auto-detect dataset type from column headers
        if source_type == "auto":
            source_type = detect_dataset_type(df)

        batch_id = str(uuid.uuid4())
        logger.info(f"Starting ingestion — file='{file_path}' type='{source_type}' batch='{batch_id}'")

        if source_type == "station_data":
            return StationDataETL.ingest(df, batch_id)
        elif source_type == "tcf_data":
            return TCFDataETL.ingest(df, batch_id)
        else:
            raise ValueError(f"Unknown source_type: '{source_type}'")

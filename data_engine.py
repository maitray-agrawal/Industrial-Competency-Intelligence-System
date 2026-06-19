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

import re

logger = get_logger("DataEngine")

# ---------------------------------------------------------------------------
# Standardized Shop Data Schema — 9 canonical columns (slug-based matching)
# ---------------------------------------------------------------------------

SHOP_DATA_SCHEMA: list[str] = [
    "SHOP",
    "CELL",
    "LINE",
    "ZONE NO",
    "STATIONS",
    "PROCESS",
    "TOOLS / EQUIPMENT",
    "OPERATION SUMMARY",
    "SKILL PART",
]

# Required for Shop Data uploads
SHOP_DATA_REQUIRED: set[str] = {"SHOP", "LINE", "STATIONS"}

# Slug (all non-alphanumeric chars removed, lowercase) -> canonical name
_SHOP_SLUG_MAP: dict[str, str] = {
    re.sub(r"[^a-z0-9]", "", col.lower()): col
    for col in SHOP_DATA_SCHEMA
}

# TCF detection constants (unchanged)
TCF_REQUIRED_COLS: set[str] = {"topic", "sub-topic"}
TCF_OPTIONAL_COLS: set[str] = {"diploma", "semester", "matched operation", "skill part"}

# TCF alias map (simple lowercase lookup, no slug needed)
_TCF_ALIASES: dict[str, str] = {
    "sub-topic":         "sub-topic",
    "subtopic":          "sub-topic",
    "sub topic":         "sub-topic",
    "matched operation":  "matched operation",
    "matched_operation":  "matched operation",
}


def _row_fingerprint(row: dict) -> str:
    """SHA-256 fingerprint of a normalised row dict for duplicate detection."""
    # Safely coerce None → '' so hash is stable and json-serializable
    canonical = str(sorted({k: (str(v) if v is not None else "") for k, v in row.items()}.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalize_shop_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map raw Excel headers to canonical Shop Data schema names using slug matching.
    Matching is case-, space-, and symbol-insensitive.

    - Known columns are renamed to their canonical uppercase form.
    - Unknown / extra / legacy columns are silently dropped.
    - Raises ValueError only if ZERO recognized columns remain after mapping.
    """
    new_cols = []
    for raw in df.columns:
        slug = re.sub(r"[^a-z0-9]", "", str(raw).lower())
        canonical = _SHOP_SLUG_MAP.get(slug)
        new_cols.append(canonical if canonical else "__drop__")
    df.columns = new_cols
    df = df.drop(columns=[c for c in df.columns if c == "__drop__"], errors="ignore")

    # Hard fail only when the file has no columns the system understands at all
    if len(df.columns) == 0:
        raise ValueError(
            "No recognized columns found in this file. "
            f"Expected at least one of: {SHOP_DATA_SCHEMA}."
        )
    return df


def _normalize_tcf_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase column names and apply TCF-specific alias map."""
    df.columns = [
        _TCF_ALIASES.get(c.strip().lower(), c.strip().lower())
        for c in df.columns
    ]
    return df


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Replace NaN with None, strip string cells."""
    df = df.where(pd.notnull(df), None)
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].apply(lambda x: x.strip() if isinstance(x, str) else x)
    return df


# Values that are considered empty regardless of type
_BLANK_SENTINELS: frozenset = frozenset({None, "", "nan", "NaN", "none", "None", "NULL", "null", "na", "NA", "N/A", "n/a"})


def _is_blank(value) -> bool:
    """
    Return True when a cell value should be treated as empty.
    Catches: None, NaN (float), empty string, and string sentinels.
    """
    import math
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if str(value).strip() in _BLANK_SENTINELS:
        return True
    return False


def _clean_val(value) -> str | None:
    """Return stripped string or None if blank."""
    return None if _is_blank(value) else str(value).strip()


def detect_dataset_type(df: pd.DataFrame) -> str:
    """
    Infer dataset type from column headers using slug matching.
    Returns 'station_data' or 'tcf_data', or raises ValueError.
    """
    slugs = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in df.columns}

    # Shop Data: required slugs for SHOP, LINE, STATIONS
    shop_required_slugs = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in SHOP_DATA_REQUIRED}
    if shop_required_slugs.issubset(slugs):
        return "station_data"

    # TCF Syllabus: must have topic + sub-topic
    tcf_required_slugs = {re.sub(r"[^a-z0-9]", "", c) for c in TCF_REQUIRED_COLS}
    if tcf_required_slugs.issubset(slugs):
        return "tcf_data"

    raise ValueError(
        f"Cannot identify dataset type. "
        f"Shop Data requires columns {sorted(SHOP_DATA_REQUIRED)}; "
        f"TCF Syllabus requires {sorted(TCF_REQUIRED_COLS)}. "
        f"Found: {list(df.columns)}."
    )


# =============================================================================
# STATION DATA ETL
# =============================================================================

class StationDataETL:
    """
    Ingests Shop Data Excel files (e.g. station layout exports from any manufacturing shop).
    Column map: shop | station | process | tools/equipment | operation summary | skill part
    """

    @staticmethod
    def _make_code(*parts: str) -> str:
        """Build a deterministic entity code from normalized name parts."""
        joined = "_".join(TaxonomyNormalizer.normalize_code(p) for p in parts if p)
        return joined[:64]

    @classmethod
    def ingest(cls, df: pd.DataFrame, batch_id: str, shop: str = "",
               upload_mode: str = "upsert") -> dict:
        """
        shop:        optional shop code override from the UI wizard.
        upload_mode: 'insert_only' | 'update_only' | 'upsert' (default)
            insert_only  — skip rows whose STATIONS already exists in DB
            update_only  — skip rows whose STATIONS does NOT exist in DB
            upsert       — insert new, update existing (recommended)
        """
        df = _normalize_shop_columns(df)
        df = _clean_df(df)

        present_cols  = set(df.columns.tolist())
        missing_cols  = [c for c in SHOP_DATA_SCHEMA if c not in present_cols]

        stats = {"shops": 0, "stations": 0, "processes": 0,
                 "operations": 0, "skills": 0, "tools": 0,
                 "inserted": 0, "updated": 0,
                 "staged": 0, "skipped": 0, "errors": 0, "warnings": 0,
                 "missing_columns": missing_cols,
                 "upload_mode": upload_mode}

        with SessionLocal() as session:
            seen_fingerprints: set[str] = set()

            for idx, row in df.iterrows():
                raw = row.to_dict()
                fp  = _row_fingerprint(raw)

                # Dedup within this batch
                if fp in seen_fingerprints:
                    stats["skipped"] += 1
                    continue
                seen_fingerprints.add(fp)

                # Stage raw row — outside savepoint so it always persists
                staging = StagingData(
                    batch_id=batch_id,
                    source_file="station_data",
                    raw_data=raw,
                    status="PENDING",
                )
                session.add(staging)
                session.flush()  # get staging.id before savepoint

                # ── Per-row savepoint — a failed row rolls back only itself ──
                sp = session.begin_nested()
                try:
                    # ── SHOP ────────────────────────────────────────────────
                    raw_shop_val = _clean_val(raw.get("SHOP")) or _clean_val(shop) or "UNKNOWN_SHOP"
                    shop_code = TaxonomyNormalizer.normalize_code(raw_shop_val)
                    shop_name = TaxonomyNormalizer.preserve_industrial_code(raw_shop_val)
                    shop_obj  = session.query(Shop).filter_by(shop_code=shop_code).first()
                    if not shop_obj:
                        shop_obj = Shop(shop_code=shop_code, name=shop_name)
                        session.add(shop_obj)
                        session.flush()
                        stats["shops"] += 1

                    # ── STATION ─────────────────────────────────────────────
                    raw_stn = _clean_val(raw.get("STATIONS"))
                    if not raw_stn:
                        logger.warning(f"Row {idx}: blank STATIONS — skipping row.")
                        staging.status = "SKIPPED"
                        stats["skipped"] += 1
                        sp.rollback()
                        continue

                    station_code = TaxonomyNormalizer.normalize_code(raw_stn)[:64]
                    station_name = TaxonomyNormalizer.preserve_industrial_code(raw_stn)
                    raw_cell     = _clean_val(raw.get("CELL"))
                    raw_line     = _clean_val(raw.get("LINE"))
                    raw_zone     = _clean_val(raw.get("ZONE NO"))

                    station = session.query(Station).filter_by(station_code=station_code).first()
                    if station:
                        # Existing record found
                        if upload_mode == "insert_only":
                            logger.debug(f"Row {idx}: station '{station_code}' exists — insert_only, skipping.")
                            staging.status = "SKIPPED"
                            stats["skipped"] += 1
                            sp.rollback()
                            continue
                        # update_only or upsert: refresh mutable fields
                        station.name      = station_name
                        station.cell      = raw_cell
                        station.line      = raw_line
                        station.zone_no   = raw_zone
                        station.row_order = int(idx)
                        station.shop_id   = shop_obj.id
                        session.flush()
                        stats["updated"] += 1
                    else:
                        # No existing record
                        if upload_mode == "update_only":
                            logger.debug(f"Row {idx}: station '{station_code}' not found — update_only, skipping.")
                            staging.status = "SKIPPED"
                            stats["skipped"] += 1
                            sp.rollback()
                            continue
                        station = Station(
                            station_code=station_code,
                            raw_station_id=raw_stn,
                            name=station_name,
                            shop_id=shop_obj.id,
                            cell=raw_cell,
                            line=raw_line,
                            zone_no=raw_zone,
                            row_order=int(idx),
                        )
                        session.add(station)
                        session.flush()
                        stats["stations"] += 1
                        stats["inserted"] += 1

                    # ── PROCESS ─────────────────────────────────────────────
                    raw_proc = _clean_val(raw.get("PROCESS"))
                    process  = None
                    if raw_proc:
                        process_code = cls._make_code(station_code, raw_proc)[:64]
                        process_name = TaxonomyNormalizer.title_case(raw_proc) or raw_proc
                        process = session.query(Process).filter_by(process_code=process_code).first()
                        if process:
                            if upload_mode != "insert_only":
                                process.name = process_name
                                session.flush()
                        else:
                            if upload_mode != "update_only":
                                process = Process(
                                    process_code=process_code,
                                    name=process_name,
                                    station_id=station.id,
                                )
                                session.add(process)
                                session.flush()
                                stats["processes"] += 1

                    # ── OPERATION ────────────────────────────────────────────
                    raw_op_summary = _clean_val(raw.get("OPERATION SUMMARY")) or ""
                    raw_skill_part = _clean_val(raw.get("SKILL PART"))  or ""
                    operation      = None
                    if process:
                        op_code   = cls._make_code(process.process_code, raw_op_summary or f"OP_{idx}")
                        operation = session.query(Operation).filter_by(operation_code=op_code).first()
                        op_name   = TaxonomyNormalizer.normalize(raw_op_summary) or process.name or f"Operation {idx}"
                        if operation:
                            if upload_mode != "insert_only":
                                operation.name             = op_name
                                operation.operation_summary = raw_op_summary or None
                                operation.skill_part       = TaxonomyNormalizer.normalize(raw_skill_part) or None
                                session.flush()
                        else:
                            if upload_mode != "update_only":
                                operation = Operation(
                                    operation_code=op_code,
                                    name=op_name,
                                    operation_summary=raw_op_summary or None,
                                    skill_part=TaxonomyNormalizer.normalize(raw_skill_part) or None,
                                    process_id=process.id,
                                )
                                session.add(operation)
                                session.flush()
                                stats["operations"] += 1

                    # ── SKILL ────────────────────────────────────────────────
                    if raw_skill_part and operation:
                        skill_code = cls._make_code("SKILL", raw_skill_part)
                        skill_name = TaxonomyNormalizer.title_case(raw_skill_part) or raw_skill_part
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
                        if not session.query(SkillOperationMap).filter_by(
                            skill_id=skill.id, operation_id=operation.id
                        ).first():
                            session.add(SkillOperationMap(
                                skill_id=skill.id, operation_id=operation.id,
                                confidence=1.0, method="keyword",
                            ))

                        # Direct link: skill ↔ station
                        if not session.query(SkillStationMap).filter_by(
                            skill_id=skill.id, station_id=station.id
                        ).first():
                            session.add(SkillStationMap(
                                skill_id=skill.id, station_id=station.id,
                                confidence=1.0, method="etl",
                            ))

                    # ── TOOLS ────────────────────────────────────────────────
                    raw_tools = _clean_val(raw.get("TOOLS / EQUIPMENT"))
                    if raw_tools:
                        for tool_name_raw in TaxonomyNormalizer.split_multi_delimited(raw_tools):
                            if _is_blank(tool_name_raw):
                                continue
                            tool_code = TaxonomyNormalizer.normalize_code(
                                TaxonomyNormalizer.normalize(tool_name_raw)
                            )[:64]
                            if not tool_code:
                                continue
                            tool_norm = TaxonomyNormalizer.title_case(tool_name_raw) or tool_name_raw.strip()
                            if not tool_norm:
                                continue
                            tool = session.query(Tool).filter_by(tool_code=tool_code).first()
                            if not tool:
                                tool = Tool(tool_code=tool_code, name=tool_norm)
                                session.add(tool)
                                session.flush()
                                stats["tools"] += 1

                            # Link tool ↔ station
                            if not session.query(ToolStationMap).filter_by(
                                tool_id=tool.id, station_id=station.id
                            ).first():
                                session.add(ToolStationMap(
                                    tool_id=tool.id, station_id=station.id,
                                ))

                    # ── Station ↔ Operation shortcut ─────────────────────────
                    if operation:
                        if not session.query(StationOperationMap).filter_by(
                            station_id=station.id, operation_id=operation.id
                        ).first():
                            session.add(StationOperationMap(
                                station_id=station.id, operation_id=operation.id,
                            ))

                    sp.commit()
                    staging.status = "PROCESSED"
                    stats["staged"] += 1

                except Exception as row_err:
                    sp.rollback()
                    warn_msg = f"Row {idx} skipped: {row_err}"
                    logger.warning(warn_msg)
                    staging.status  = "FAILED"
                    staging.error_log = str(row_err)
                    stats["errors"]  += 1
                    stats["warnings"] += 1

            # Commit all staging records and successful rows together
            try:
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Station ETL final commit error: {e}")
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
        df = _normalize_tcf_columns(df)
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
    def ingest_excel(file_path: str, source_type: str = "auto",
                     shop: str = "", upload_mode: str = "upsert") -> dict:
        """
        Read an Excel file and run the appropriate ETL pipeline.

        Args:
            file_path:   Absolute path to the .xlsx / .xls file.
            source_type: 'auto' | 'station_data' | 'tcf_data'
            shop:        Optional shop code from the UI wizard (e.g. 'TCF_1').
            upload_mode: 'insert_only' | 'update_only' | 'upsert' (default)

        Returns:
            dict with ingestion stats including inserted/updated counts.
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
        logger.info(f"Starting ingestion — file='{file_path}' type='{source_type}' shop='{shop or 'N/A'}' batch='{batch_id}'")

        if source_type == "station_data":
            return StationDataETL.ingest(df, batch_id, shop=shop, upload_mode=upload_mode)
        elif source_type == "tcf_data":
            return TCFDataETL.ingest(df, batch_id)
        else:
            raise ValueError(f"Unknown source_type: '{source_type}'")

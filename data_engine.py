import os
import uuid
import hashlib
import pandas as pd
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_
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
# Standardized Shop Data Schema — 9 canonical columns (normalized keys)
# ---------------------------------------------------------------------------

# Display canonical column names (used in user-facing messages)
SHOP_DATA_DISPLAY: list[str] = [
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

# Normalized snake_case keys derived from display names. These are the
# column names the ETL will use internally (case + punctuation insensitive).
def _to_normalized(key: str) -> str:
    k = re.sub(r"[^a-z0-9]", "_", key.lower())
    k = re.sub(r"_+", "_", k).strip("_")
    return k

SHOP_DATA_SCHEMA: list[str] = [_to_normalized(c) for c in SHOP_DATA_DISPLAY]

# Required for Shop Data uploads (normalized keys)
SHOP_DATA_REQUIRED: set[str] = {"shop", "stations"}

# Slug (all non-alphanumeric chars removed, lowercase) -> normalized snake_case
_SHOP_SLUG_MAP: dict[str, str] = {
    re.sub(r"[^a-z0-9]", "", col.lower()): _to_normalized(col)
    for col in SHOP_DATA_DISPLAY
}

# ---------------------------------------------------------------------------
# Non-destructive alias mapping from UI/shop codes (underscore form)
# to the display keys used in SHOP_SCHEMAS (space form).
SHOP_ALIASES: dict[str, str] = {
    "X1_BIW":       "X1 BIW",
    "Q5_BIW":       "Q5 BIW",
    "X4_BIW":       "X4 BIW",
    "NOVA_BIW":     "NOVA BIW",
    "PAINT_SHOP":   "PAINT SHOP",
    "EV_SHOP":      "EV SHOP",
    "ENGINE_SHOP":  "ENGINE SHOP",
    "TRANSAXLE_SHOP": "TRANSAXLE SHOP",
    "PRESS_SHOP":   "PRESS SHOP",
    "TCF_1":        "TCF 1",
    "TCF_2":        "TCF 2",
    "JLR_SHOP":     "TJLR",
}

# ---------------------------------------------------------------------------
# Shop-specific schema definitions. Keys are uppercased shop identifiers
# expected from the admin UI `shop` field. Values are lists of display
# column names for that shop's export format.
# ---------------------------------------------------------------------------
SHOP_SCHEMAS: dict[str, list[str]] = {
    "X1 BIW": [
        "LINE", "ZONE NO.", "ZONE", "DESCRIPTION", "STATION NO.", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "PAINT SHOP": [
        "LINE", "ZONE NO.", "ZONE", "DESCRIPTION", "STATION NO.", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "EV SHOP": [
        "CELL", "LINE", "ZONE NO", "ZONE", "STATION NO.", "STATION DESCRIPTION",
        "PROCESS", "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "Q5 BIW": [
        "CELL", "LINE", "ZONE NO", "ZONE", "STATION NO.", "STATION DESCRIPTION",
        "PROCESS", "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "X4 BIW": [
        "CELL", "LINE", "ZONE", "STATION NO.", "STATION DESCRIPTION",
        "PROCESS", "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "NOVA BIW": [
        "LINE", "STATION NO.", "ZONE", "STATION DESCRIPTION",
        "PROCESS", "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "TCF 1": [
        "SHOP", "CELL", "LINE", "ZONE NO", "STATIONS", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "ENGINE SHOP": [
        "SHOP", "CELL", "LINE", "ZONE NO", "STATIONS", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "TRANSAXLE SHOP": [
        "SHOP", "CELL", "LINE", "ZONE NO", "STATIONS", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "PRESS SHOP": [
        "SHOP", "CELL", "LINE", "ZONE NO", "STATIONS", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
    "TCF 2": [
        "SHOP", "STATIONS", "PROCESS", "TOOLS / EQUIPMENT",
        "OPERATION SUMMARY", "SKILL PART",
    ],
    "TJLR": [
        "SHOP", "CELL", "LINE", "ZONE NO", "STATIONS", "PROCESS",
        "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART",
    ],
}

def _normalize_columns_with_schema(df: pd.DataFrame, schema_display: list[str]) -> pd.DataFrame:
    """
    Map raw DataFrame headers to normalized snake_case keys based on a
    provided shop-specific `schema_display` list. Matching is case-,
    space-, dot-, underscore- and punctuation-insensitive. Unknown
    columns are ignored (dropped).
    """
    def _slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    HEADER_CANONICAL_MAP = {
        "shop": "shop",
        "cell": "cell",
        "line": "line",
        "zone": "zone_no",
        "zonen": "zone_no",
        "zonenumber": "zone_no",
        "zoneno": "zone_no",
        "stage": "process",
        "process": "process",
        "st": "stations",
        "stn": "stations",
        "stno": "stations",
        "station": "stations",
        "stations": "stations",
        "stationno": "stations",
        "stationnumber": "stations",
        "stationdescription": "station_description",
        "description": "station_description",
        "zonedescription": "zone_description",
        "activitydescription": "operation_summary",
        "operationdescription": "operation_summary",
        "operationsummary": "operation_summary",
        "skillpart": "skill_part",
        "skill": "skill_part",
        "toolsequipment": "tools_equipment",
        "toolsequip": "tools_equipment",
        "tools": "tools_equipment",
        "equipment": "tools_equipment",
    }

    schema_map = {
        _slug(col): _to_normalized(col)
        for col in schema_display
    }

    new_cols = []
    for raw in df.columns:
        slug = _slug(raw)
        if slug in HEADER_CANONICAL_MAP:
            new_cols.append(HEADER_CANONICAL_MAP[slug])
        elif slug in schema_map:
            new_cols.append(schema_map[slug])
        else:
            new_cols.append("__drop__")

    df = df.copy()
    df.columns = new_cols
    df = df.drop(columns=[c for c in df.columns if c == "__drop__"], errors="ignore")
    return df

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

    - Known columns are renamed to their canonical internal key.
    - Unknown / extra / legacy columns are silently dropped.
    - Raises ValueError only if ZERO recognized columns remain after mapping.
    """
    # Allow normalized keys produced by shop-specific mapping or present
    # in other shop schemas. Build allowed normalized names set.
    allowed_normalized = set(SHOP_DATA_SCHEMA)
    for schema in SHOP_SCHEMAS.values():
        for c in schema:
            allowed_normalized.add(_to_normalized(c))
    allowed_normalized.update({"station_description", "zone_description"})

    HEADER_CANONICAL_MAP = {
        "shop": "shop",
        "cell": "cell",
        "line": "line",
        "zone": "zone_no",
        "zonen": "zone_no",
        "zonenumber": "zone_no",
        "zoneno": "zone_no",
        "stage": "process",
        "process": "process",
        "st": "stations",
        "stn": "stations",
        "stno": "stations",
        "station": "stations",
        "stations": "stations",
        "stationno": "stations",
        "stationnumber": "stations",
        "stationdescription": "station_description",
        "description": "station_description",
        "zonedescription": "zone_description",
        "activitydescription": "operation_summary",
        "operationdescription": "operation_summary",
        "operationsummary": "operation_summary",
        "skillpart": "skill_part",
        "skill": "skill_part",
        "toolsequipment": "tools_equipment",
        "toolsequip": "tools_equipment",
        "tools": "tools_equipment",
        "equipment": "tools_equipment",
    }

    new_cols = []
    for raw in df.columns:
        slug = re.sub(r"[^a-z0-9]", "", str(raw).lower())
        normalized = _SHOP_SLUG_MAP.get(slug) or HEADER_CANONICAL_MAP.get(slug)
        if normalized:
            new_cols.append(normalized)
            continue

        cand = _to_normalized(str(raw))
        if cand in allowed_normalized:
            new_cols.append(cand)
            continue

        new_cols.append("__drop__")

    df.columns = new_cols
    df = df.drop(columns=[c for c in df.columns if c == "__drop__"], errors="ignore")

    if len(df.columns) == 0:
        raise ValueError(
            "No recognized columns found in this file. "
            f"Expected at least one of: {SHOP_DATA_DISPLAY}."
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
    Returns 'station_data', 'station_details', or 'tcf_data'.
    """
    slugs = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in df.columns}

    # Syllabus: must have topic + sub-topic
    tcf_required_slugs = {re.sub(r"[^a-z0-9]", "", c) for c in TCF_REQUIRED_COLS}
    if tcf_required_slugs.issubset(slugs):
        return "tcf_data"

    # Station Details upload: SHOP + STATION present (enrichment file)
    if {"shop", "station"}.issubset(slugs) or {"shop", "stations"}.issubset(slugs):
        return "station_details"

    # Station Data: at least one station identifier plus any other shop data column.
    station_slugs = {"st", "stn", "stno", "station", "stations", "stationno", "stationnumber"}
    shop_data_slugs = station_slugs.union({"shop", "cell", "line", "zone", "zonen", "zonenumber", "zoneno", "process", "stage", "tools", "equipment", "skill", "skillpart", "description"})
    if slugs.intersection(station_slugs) and slugs.intersection(shop_data_slugs):
        return "station_data"

    raise ValueError(
        "Cannot identify dataset type. "
        f"Shop Data requires station identifiers and at least one shop field; "
        f"Syllabus requires {sorted(TCF_REQUIRED_COLS)}. "
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
                    raw_shop_val = _clean_val(raw.get("shop")) or _clean_val(shop) or "UNKNOWN_SHOP"
                    shop_code = TaxonomyNormalizer.normalize_code(raw_shop_val)
                    shop_name = TaxonomyNormalizer.preserve_industrial_code(raw_shop_val)
                    shop_obj  = session.query(Shop).filter_by(shop_code=shop_code).first()
                    if not shop_obj:
                        shop_obj = Shop(shop_code=shop_code, name=shop_name)
                        session.add(shop_obj)
                        session.flush()
                        stats["shops"] += 1

                    # ── STATION ─────────────────────────────────────────────
                    raw_stn = _clean_val(
                        raw.get("stations") or raw.get("station_no") or raw.get("station")
                    )
                    if not raw_stn:
                        logger.warning(f"Row {idx}: blank stations — skipping row.")
                        staging.status = "SKIPPED"
                        stats["skipped"] += 1
                        sp.rollback()
                        continue

                    raw_station_desc = _clean_val(
                        raw.get("station_description") or raw.get("description")
                    )
                    station_code = TaxonomyNormalizer.normalize_code(raw_stn)[:64]
                    station_name = TaxonomyNormalizer.preserve_industrial_code(raw_station_desc or raw_stn)
                    raw_cell     = _clean_val(raw.get("cell"))
                    raw_line     = _clean_val(raw.get("line"))
                    raw_zone     = _clean_val(raw.get("zone_no"))

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
                    raw_proc = _clean_val(raw.get("process") or raw.get("stage"))
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
                    raw_op_summary = _clean_val(
                        raw.get("operation_summary") or raw.get("activity_description")
                    ) or ""
                    raw_skill_part = _clean_val(raw.get("skill_part"))  or ""
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
                    raw_tools = _clean_val(
                        raw.get("tools_equipment") or raw.get("tools") or raw.get("equipment")
                    )
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
# STATION DETAILS ETL (enrichment-only)
# =============================================================================

class StationDetailsETL:
    """
    Ingests station-details enrichment files. Only enriches existing
    stations — does NOT create shops, lines, or zones. Missing columns
    are allowed and unknown columns ignored.
    """

    @staticmethod
    def _make_code(*parts: str) -> str:
        joined = "_".join(TaxonomyNormalizer.normalize_code(p) for p in parts if p)
        return joined[:64]

    @classmethod
    def ingest(cls, df: pd.DataFrame, batch_id: str, shop: str = "", upload_mode: str = "upsert") -> dict:
        df = _clean_df(df)

        # Normalize column names to lower/slug forms used by this module
        cols = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in df.columns]
        expected = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in [
            "SHOP", "STATION", "PROCESS", "TOOLS / EQUIPMENT", "OPERATION SUMMARY", "SKILL PART"
        ]}
        missing_cols = [v for k, v in expected.items() if k not in cols]

        stats = {"updated": 0, "processes": 0, "tools": 0, "skills": 0, "skipped": 0, "errors": 0, "missing_columns": missing_cols}

        with SessionLocal() as session:
            seen_fps = set()
            for idx, row in df.iterrows():
                raw = row.to_dict()
                fp = _row_fingerprint(raw)
                if fp in seen_fps:
                    stats["skipped"] += 1
                    continue
                seen_fps.add(fp)

                # Stage a minimal staging record so admin can audit
                staging = StagingData(batch_id=batch_id, source_file="station_details", raw_data=raw, status="PENDING")
                session.add(staging)
                session.flush()

                sp = session.begin_nested()
                try:
                    raw_shop_val = _clean_val(raw.get("SHOP")) or _clean_val(shop) or ""
                    raw_stn = _clean_val(raw.get("STATION")) or _clean_val(raw.get("STATIONS"))

                    if not raw_shop_val or not raw_stn:
                        staging.status = "SKIPPED"
                        stats["skipped"] += 1
                        sp.rollback()
                        continue

                    shop_code = TaxonomyNormalizer.normalize_code(raw_shop_val)
                    shop_obj = session.query(Shop).filter_by(shop_code=shop_code).first()
                    if not shop_obj:
                        # Do NOT create shops for station-details upload
                        staging.status = "SKIPPED"
                        stats["skipped"] += 1
                        sp.rollback()
                        continue

                    # Locate station by raw_station_id or station_code within this shop
                    station = session.query(Station).filter_by(shop_id=shop_obj.id).filter(
                        or_(Station.raw_station_id == raw_stn, Station.station_code == TaxonomyNormalizer.normalize_code(raw_stn))
                    ).first()

                    if not station:
                        # Do not create stations here
                        staging.status = "SKIPPED"
                        stats["skipped"] += 1
                        sp.rollback()
                        continue

                    # Update station display/name if provided (preserve otherwise)
                    if raw_stn and station.name != raw_stn:
                        station.name = raw_stn

                    # --- PROCESS ---
                    raw_proc = _clean_val(raw.get("PROCESS"))
                    process = None
                    if raw_proc:
                        proc_code = cls._make_code(station.station_code, raw_proc)[:64]
                        process = session.query(Process).filter_by(process_code=proc_code).first()
                        if not process:
                            process = Process(process_code=proc_code, name=TaxonomyNormalizer.title_case(raw_proc), station_id=station.id)
                            session.add(process)
                            session.flush()
                            stats["processes"] += 1

                    # --- OPERATION ---
                    raw_op_summary = _clean_val(raw.get("OPERATION SUMMARY")) or ""
                    operation = None
                    if raw_op_summary and process:
                        op_code = cls._make_code(process.process_code, raw_op_summary)[:64]
                        operation = session.query(Operation).filter_by(operation_code=op_code).first()
                        op_name = TaxonomyNormalizer.normalize(raw_op_summary) or process.name
                        if operation:
                            if upload_mode != "insert_only":
                                operation.name = op_name
                                operation.operation_summary = raw_op_summary
                                session.flush()
                        else:
                            if upload_mode != "update_only":
                                operation = Operation(operation_code=op_code, name=op_name, operation_summary=raw_op_summary, process_id=process.id)
                                session.add(operation)
                                session.flush()

                    # --- TOOLS ---
                    raw_tools = _clean_val(raw.get("TOOLS / EQUIPMENT")) or _clean_val(raw.get("TOOLS_EQUIPMENT"))
                    if raw_tools:
                        for tool_name in TaxonomyNormalizer.split_multi_delimited(raw_tools):
                            if _is_blank(tool_name):
                                continue
                            tool_code = TaxonomyNormalizer.normalize_code(TaxonomyNormalizer.normalize(tool_name))[:64]
                            if not tool_code:
                                continue
                            tool = session.query(Tool).filter_by(tool_code=tool_code).first()
                            if not tool:
                                tool = Tool(tool_code=tool_code, name=TaxonomyNormalizer.title_case(tool_name) or tool_name)
                                session.add(tool)
                                session.flush()
                                stats["tools"] += 1

                            if not session.query(ToolStationMap).filter_by(tool_id=tool.id, station_id=station.id).first():
                                session.add(ToolStationMap(tool_id=tool.id, station_id=station.id))

                    # --- SKILL ---
                    raw_skill_part = _clean_val(raw.get("SKILL PART")) or _clean_val(raw.get("SKILL_PART"))
                    if raw_skill_part:
                        skill_code = cls._make_code("SKILL", raw_skill_part)
                        skill = session.query(Skill).filter_by(skill_code=skill_code).first()
                        if not skill:
                            skill = Skill(skill_code=skill_code, name=TaxonomyNormalizer.title_case(raw_skill_part), skill_part=TaxonomyNormalizer.normalize(raw_skill_part))
                            session.add(skill)
                            session.flush()
                            stats["skills"] += 1

                        if operation and not session.query(SkillOperationMap).filter_by(skill_id=skill.id, operation_id=operation.id).first():
                            session.add(SkillOperationMap(skill_id=skill.id, operation_id=operation.id, confidence=1.0, method="keyword"))

                        if not session.query(SkillStationMap).filter_by(skill_id=skill.id, station_id=station.id).first():
                            session.add(SkillStationMap(skill_id=skill.id, station_id=station.id, confidence=1.0, method="etl"))

                    session.flush()
                    sp.commit()
                    staging.status = "PROCESSED"
                    stats["updated"] += 1

                except Exception as err:
                    sp.rollback()
                    staging.status = "FAILED"
                    staging.error_log = str(err)
                    stats["errors"] += 1

            try:
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise

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

        # If admin selected a shop, apply shop-specific schema mapping first
        if shop and isinstance(shop, str):
            shop_key = shop.strip().upper()
            schema_key = SHOP_ALIASES.get(shop_key)
            if not schema_key:
                schema_key = SHOP_ALIASES.get(shop_key.replace(" ", "_"), shop_key)
            if not schema_key and shop_key.replace("_", " ") in SHOP_SCHEMAS:
                schema_key = shop_key.replace("_", " ")
            if not schema_key and shop_key in SHOP_SCHEMAS:
                schema_key = shop_key
            logger.info(f"Shop='{shop_key}' Schema='{schema_key}'")
            schema = SHOP_SCHEMAS.get(schema_key)
            if schema:
                logger.info(f"Applying shop-specific schema for '{schema_key}'")
                df = _normalize_columns_with_schema(df, schema)
                # treat as station data when shop-specific schema applied
                source_type = "station_data"

        # Auto-detect dataset type from column headers (when not overridden)
        if source_type == "auto":
            source_type = detect_dataset_type(df)

        batch_id = str(uuid.uuid4())
        logger.info(f"Starting ingestion — file='{file_path}' type='{source_type}' shop='{shop or 'N/A'}' batch='{batch_id}'")

        if source_type == "station_data":
            return StationDataETL.ingest(df, batch_id, shop=shop, upload_mode=upload_mode)
        elif source_type == "station_details":
            return StationDetailsETL.ingest(df, batch_id, shop=shop, upload_mode=upload_mode)
        elif source_type == "tcf_data":
            return TCFDataETL.ingest(df, batch_id)
        else:
            raise ValueError(f"Unknown source_type: '{source_type}'")

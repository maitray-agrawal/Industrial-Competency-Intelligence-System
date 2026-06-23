import os
import re
import atexit
import hashlib
import uuid as _uuid
from flask import Flask, render_template, request, Response, redirect, url_for, flash, jsonify, session as flask_session, send_file
from werkzeug.utils import secure_filename

from database import SessionLocal, engine, init_db
from models import (
    Shop, Station, Process, Operation, Skill, Tool,
    Topic, Subtopic, StagingData, CompetencyMap,
    SkillOperationMap, TopicSkillMap, ToolStationMap, UploadedFile,
    SkillStationMap, StationOperationMap,
    GraphEntity, GraphRelationship,
    ShopWISDocument, StationWISDocument
)
from sqlalchemy import Integer as _SAInteger
from search_engine import SearchAPI, SearchIndexer
from data_engine import IngestionPipeline
from heuristic_engine import KnowledgeMapper
from competency_engine import CompetencyEngine
from logger import get_logger
from api_contracts import build_unified_search_entity
from sqlalchemy import text, func

logger = get_logger("FlaskApp")

# ---------------------------------------------------------------------------
# App Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("IIK_SECRET_KEY", "industrial-offline-secret-2025")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
ALLOWED_WIS_EXTENSIONS = {".ppt", ".pptx"}

DEBUG_MODE = os.environ.get("FLASK_ENV", "production").lower() == "development"

def allowed_file(filename: str) -> bool:
    """Return True only if the file has an allowed Excel extension."""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def allowed_wis_file(filename: str) -> bool:
    """Return True only if the file has an allowed PowerPoint extension."""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_WIS_EXTENSIONS


# ---------------------------------------------------------------------------
# Auth — HTTP Basic Auth for admin panel
# ---------------------------------------------------------------------------
ADMIN_USER = os.environ.get("IIK_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("IIK_ADMIN_PASS", "tata@1945")


def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASS


def authenticate():
    return Response(
        "Authentication required.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="IIK Admin"'},
    )


def requires_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 error: {e}")
    return render_template("error.html", code=500, message="Internal server error."), 500


@app.errorhandler(413)
def too_large(e):
    flash("File too large. Maximum upload size is 16 MB.", "danger")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Helper — system stats for dashboard
# ---------------------------------------------------------------------------
def _get_system_stats() -> dict:
    with SessionLocal() as db:
        return {
            # Exclude the virtual/wrapper WELD_SHOP from visible shop counts
            "shops":      db.query(Shop).filter(Shop.shop_code != "WELD_SHOP").count(),
            "stations":   db.query(Station).count(),
            "processes":  db.query(Process).count(),
            "operations": db.query(Operation).count(),
            "skills":     db.query(Skill).count(),
            "tools":      db.query(Tool).count(),
            "topics":     db.query(Topic).count(),
            "subtopics":  db.query(Subtopic).count(),
            "mappings":   db.query(CompetencyMap).count(),
        }


# ===========================================================================
# ROUTES — Public
# ===========================================================================

@app.route("/")
def index():
    stats = _get_system_stats()
    competency_summary = CompetencyEngine.get_all_stations_summary()
    return render_template("index.html", stats=stats, competency_summary=competency_summary)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    entity_filter = request.args.get("type", None)
    results = []
    if q:
        results = SearchAPI.search(q, limit=20, entity_filter=entity_filter or None)
    return render_template(
        "search.html", query=q, results=results, entity_filter=entity_filter
    )


@app.route("/station/<int:station_id>")
def station_detail(station_id):
    profile = CompetencyEngine.get_station_knowledge_profile(station_id)
    if "error" in profile:
        flash(profile["error"], "danger")
        return redirect(url_for("index"))

    # --- shop_code for breadcrumb ---
    shop_code = None
    with SessionLocal() as _s:
        _stn = _s.get(Station, station_id)
        if _stn and _stn.shop:
            shop_code = _stn.shop.shop_code

    # --- skills_summary: unique skills with coverage status ---
    readiness      = profile.get("readiness", {})
    missing_names  = {s["skill_name"] for s in readiness.get("missing_skills", [])}
    seen_sk        = set()
    skills_summary = []
    for proc in profile.get("processes", []):
        for op in proc.get("operations", []):
            for sk in op.get("skills", []):
                if sk not in seen_sk:
                    seen_sk.add(sk)
                    skills_summary.append({"name": sk, "covered": sk not in missing_names})
    skills_summary.sort(key=lambda x: (not x["covered"], x["name"]))

    # --- graph nodes + edges for vis-network ---
    stn_nid    = f"stn_{station_id}"
    graph_nodes = [{"id": stn_nid, "label": profile.get("station_code", "STN"),
                    "group": "station", "size": 32}]
    graph_edges = []

    for i, proc in enumerate(profile.get("processes", [])):
        pid   = f"proc_{i}"
        label = proc["name"][:24] + ("\u2026" if len(proc["name"]) > 24 else "")
        graph_nodes.append({"id": pid, "label": label, "group": "process"})
        graph_edges.append({"from": stn_nid, "to": pid})

    for sk in skills_summary[:14]:
        sid   = f"sk_{hash(sk['name']) & 0xFFFFFF}"
        label = sk["name"][:22] + ("\u2026" if len(sk["name"]) > 22 else "")
        graph_nodes.append({"id": sid, "label": label,
                             "group": "skill_ok" if sk["covered"] else "skill_gap"})
        graph_edges.append({"from": stn_nid, "to": sid})

    for topic in readiness.get("covered_topics", [])[:7]:
        tid   = f"topic_{topic['topic_id']}"
        label = topic["topic_title"][:24] + ("\u2026" if len(topic["topic_title"]) > 24 else "")
        graph_nodes.append({"id": tid, "label": label, "group": "topic"})
        graph_edges.append({"from": tid, "to": stn_nid})

    # --- WIS documents for this station ---
    wis_docs = None
    is_admin = False
    with SessionLocal() as _s:
        wis_docs = _s.query(StationWISDocument).filter_by(station_id=station_id).order_by(StationWISDocument.uploaded_at.desc()).all()
        is_admin = request.authorization and check_auth(request.authorization.username, request.authorization.password)

    return render_template(
        "station.html",
        profile        = profile,
        shop_code      = shop_code,
        skills_summary = skills_summary,
        graph_nodes    = graph_nodes,
        graph_edges    = graph_edges,
        wis_docs       = wis_docs,
        is_admin       = is_admin,
    )


@app.route("/graph")
def graph():
    stats = _get_system_stats()
    return render_template("graph.html", stats=stats)

# ---------------------------------------------------------------------------
# Helper — parse raw_station_id into Line / Zone / Station segments
# ---------------------------------------------------------------------------
def _fmt_zone(zone_raw: str) -> str:
    """Format a raw zone value (e.g. '1.0', '2', 'Zone 3') into a clean integer string."""
    if not zone_raw:
        return ""
    # Strip trailing .0 from float-like strings (e.g. '1.0' → '1')
    try:
        return str(int(float(zone_raw)))
    except (ValueError, TypeError):
        return zone_raw.strip()


def _parse_station_hierarchy(stations, shop_code: str | None = None):
    """
    Accepts a list of Station ORM objects (already ordered by row_order from the
    query) and returns a structured hierarchy that PRESERVES Excel insertion order.
    Grouping adapts to `shop_code`. Top-level entries returned have the shape
    {label, zones: [{label, zone_badge, stations: [...]}, ...]}. The parser
    selects the most-relevant non-empty fields per shop type and never
    creates empty hierarchy levels.
    """
    shop_map = {
        "TCF_1":       ["cell", "line", "zone_no"],
        "ENGINE_SHOP": ["cell", "line", "zone_no"],
        "TRANSAXLE_SHOP": ["cell", "line", "zone_no"],
        "JLR":        ["cell", "line", "zone_no"],
        "EV_SHOP":     ["cell", "line", "zone_no"],
        "X4_BIW":      ["cell", "line", "zone_no"],
        "X1_BIW":      ["line", "zone_no"],
        "PAINT_SHOP":  ["line", "zone_no"],
        "Q5_BIW":      ["line", "zone_no"],
        "NOVA_BIW":    ["line"],
        "TCF_2":       ["shop"],
    }

    def _norm_shop_key(code: str | None) -> str:
        if not code:
            return ""
        return str(code).strip().upper()

    def _group_label(preferred_fields: list[str]) -> str:
        if not preferred_fields:
            return "Group"
        field = preferred_fields[0]
        if field == "cell":
            return "Cell"
        if field == "line":
            return "Line"
        if field == "shop":
            return "Shop"
        if field == "zone_no":
            return "Zone"
        return field.replace("_", " ").title()

    key = _norm_shop_key(shop_code)
    preferred = shop_map.get(key, ["cell", "line", "zone_no"])  # default
    group_label = _group_label(preferred)

    def _val(stn, fld):
        if fld == "cell":
            return (stn.cell or "").strip()
        if fld == "line":
            return (stn.line or "").strip()
        if fld == "zone_no":
            return (stn.zone_no or "").strip()
        if fld == "stations":
            return (stn.raw_station_id or stn.station_code or "").strip()
        return str(getattr(stn, fld, "") or "").strip()

    order = []
    groups = {}

    for stn in stations:
        vals = [_val(stn, f) for f in preferred]
        path = [v for v in vals if v]

        if not path:
            top = ""
            zone = ""
        else:
            top = path[0]
            zone = path[1] if len(path) > 1 else ""

        gkey = (top, zone)
        if gkey not in groups:
            order.append(gkey)
            groups[gkey] = []

        station_item = {
            "id": stn.id,
            "raw_id": stn.raw_station_id or stn.station_code or str(stn.id),
            "name": stn.name,
        }

        processes = []
        for proc in getattr(stn, "processes", []) or []:
            processes.append({
                "id": proc.id,
                "name": proc.name,
                "operation_count": len(getattr(proc, "operations", []) or []),
            })
        if processes:
            station_item["processes"] = processes

        groups[gkey].append(station_item)

    tops = []
    top_to_zones = {}
    for top, zone in order:
        if top not in tops:
            tops.append(top)
            top_to_zones[top] = []
        top_to_zones[top].append((zone, groups[(top, zone)]))

    hierarchy = []
    for top in tops:
        zones = []
        for zone_label, stns in top_to_zones[top]:
            zones.append({
                "label": zone_label,
                "zone_badge": _fmt_zone(zone_label) if zone_label else "",
                "stations": stns,
            })
        hierarchy.append({
            "label": top,
            "zones": zones,
        })

    return hierarchy, group_label


# ---------------------------------------------------------------------------
# Weld Shop — BIW sub-level constants and parsers
# ---------------------------------------------------------------------------

# Canonical BIW group order (as specified by the user)
_WELD_BIW_ORDER  = ["X1_BIW", "Q5_BIW", "X4_BIW", "NOVA_BIW"]

_WELD_BIW_META = {
    "X1_BIW":   {"label": "X1 BIW",   "icon": "🚗", "desc": "X1 Body-in-White Assembly"},
    "Q5_BIW":   {"label": "Q5 BIW",   "icon": "🚙", "desc": "Q5 Body-in-White Assembly"},
    "X4_BIW":   {"label": "X4 BIW",   "icon": "🛻", "desc": "X4 Body-in-White Assembly"},
    "NOVA_BIW": {"label": "NOVA BIW", "icon": "⭐", "desc": "Nova Body-in-White Assembly"},
}

# Regex: extract BIW model token from Weld station code
# e.g. WLD_X1_BIW_FR_Z1_S10 → group(1) = 'X1'
_WELD_BIW_EXTRACT = re.compile(r'^WLD_([A-Z0-9]+)_BIW', re.IGNORECASE)

# Regex: within a BIW group, extract Line and Zone
# e.g. WLD_X1_BIW_FR_Z1_S10 → line='FR', zone='1'
_WELD_BIW_SUBHIER = re.compile(r'_BIW_(?P<line>[A-Z]+)_Z(?P<zone>[0-9]+)', re.IGNORECASE)


def _get_weld_biw_subgroups(stations):
    """
    Extract the BIW model groups present in a Weld Shop station list.
    Returns a list of dicts: [{key, label, icon, desc, station_count}]
    ordered by _WELD_BIW_ORDER.
    """
    counts = {}
    for stn in stations:
        raw_id = stn.raw_station_id or stn.station_code or ""
        m = _WELD_BIW_EXTRACT.match(raw_id)
        if m:
            key = f"{m.group(1).upper()}_BIW"
            counts[key] = counts.get(key, 0) + 1

    result = []
    for key in _WELD_BIW_ORDER:
        if key in counts:
            meta = _WELD_BIW_META.get(key, {"label": key.replace("_", " "), "icon": "🔩", "desc": ""})
            result.append({
                "key":           key,
                "label":         meta["label"],
                "icon":          meta["icon"],
                "desc":          meta["desc"],
                "station_count": counts[key],
            })
    return result or None


def _parse_weld_biw_subhierarchy(stations, biw_key):
    """
    Parse Weld Shop stations belonging to one BIW group into a structured
    hierarchy. Delegates to _parse_station_hierarchy to preserve Excel row order.
    """
    model = biw_key.replace("_BIW", "").upper()   # e.g. 'X1'
    prefix = f"WLD_{model}_BIW_"                   # e.g. 'WLD_X1_BIW_'

    filtered = [
        stn for stn in stations
        if (stn.raw_station_id or "").upper().startswith(prefix)
    ]

    return _parse_station_hierarchy(filtered)


@app.route("/shop/<shop_code>")
def shop_detail(shop_code):
    with SessionLocal() as db:
        shop = db.query(Shop).filter_by(shop_code=shop_code).first()
        if not shop:
            flash(f"Shop '{shop_code}' not found.", "danger")
            return redirect(url_for("index"))
        stations = db.query(Station).filter_by(shop_id=shop.id).order_by(Station.row_order, Station.id).all()
        wis_docs = db.query(ShopWISDocument).filter_by(shop_id=shop.id).order_by(ShopWISDocument.uploaded_at.desc()).all()
        is_admin = request.authorization and check_auth(request.authorization.username, request.authorization.password)

        # Weld Shop: show BIW group selector cards first
        if shop_code == "WELD_SHOP":
            subgroups = _get_weld_biw_subgroups(stations)
            if subgroups:
                return render_template(
                    "shop.html", shop=shop,
                    subgroups=subgroups, hierarchy=None, subgroup=None,
                    wis_docs=wis_docs, is_admin=is_admin,
                )

        hierarchy, group_label = _parse_station_hierarchy(stations, shop.shop_code)
        return render_template(
            "shop.html", shop=shop,
            hierarchy=hierarchy, group_label=group_label, subgroups=None, subgroup=None,
            wis_docs=wis_docs, is_admin=is_admin,
        )


@app.route("/shop/<shop_code>/<subgroup>")
def shop_subgroup(shop_code, subgroup):
    """Renders the tree for a shop sub-group (e.g. Weld BIW model)."""
    with SessionLocal() as db:
        shop = db.query(Shop).filter_by(shop_code=shop_code).first()
        if not shop:
            flash(f"Shop '{shop_code}' not found.", "danger")
            return redirect(url_for("index"))
        stations = db.query(Station).filter_by(shop_id=shop.id).order_by(Station.row_order, Station.id).all()
        wis_docs = db.query(ShopWISDocument).filter_by(shop_id=shop.id).order_by(ShopWISDocument.uploaded_at.desc()).all()
        is_admin = request.authorization and check_auth(request.authorization.username, request.authorization.password)

        if shop_code == "WELD_SHOP":
            hierarchy, group_label = _parse_weld_biw_subhierarchy(stations, subgroup)
            meta = _WELD_BIW_META.get(subgroup, {})
            subgroup_label = meta.get("label", subgroup.replace("_", " "))
        else:
            hierarchy, group_label = _parse_station_hierarchy(stations, shop.shop_code)
            subgroup_label = subgroup.replace("_", " ")

        return render_template(
            "shop.html", shop=shop,
            hierarchy=hierarchy, group_label=group_label, subgroups=None, subgroup=subgroup_label,
            wis_docs=wis_docs, is_admin=is_admin,
        )


@app.route("/api/shop/<shop_code>/hierarchy")
def api_shop_hierarchy(shop_code):
    with SessionLocal() as db:
        shop = db.query(Shop).filter_by(shop_code=shop_code).first()
        if not shop:
            return jsonify({"error": f"Shop '{shop_code}' not found"}), 404
        stations = db.query(Station).filter_by(shop_id=shop.id).order_by(Station.row_order, Station.id).all()
        hierarchy, group_label = _parse_station_hierarchy(stations, shop.shop_code)
        return jsonify({
            "shop_code": shop.shop_code,
            "shop_name": shop.name,
            "hierarchy": hierarchy,
            "group_label": group_label,
        })

@app.route("/api/competency/<int:station_id>")
def api_competency(station_id):
    return jsonify(CompetencyEngine.score_station_readiness(station_id))


@app.route("/api/recommend/<int:station_id>")
def api_recommend(station_id):
    readiness = CompetencyEngine.score_station_readiness(station_id)
    missing_ids = [s["skill_id"] for s in readiness.get("missing_skills", [])]
    recommendations = CompetencyEngine.get_recommended_modules(missing_ids)
    return jsonify(recommendations)


@app.route("/api/stats")
def api_stats():
    return jsonify(_get_system_stats())


@app.route("/api/entity-details/<entity_type>/<entity_id>")
def api_entity_details(entity_type, entity_id):
    """
    Polymorphic endpoint resolving incoming compound string tokens (e.g., 'tool-21')
    to database integer primary keys safely to support all four parameter matrices.
    """
    try:
        if isinstance(entity_id, str) and "-" in entity_id:
            clean_id = int(entity_id.split("-")[-1])
        else:
            clean_id = int(entity_id)
    except (ValueError, IndexError, TypeError):
        logger.error(f"Malformed structural routing key received: {entity_id}")
        return jsonify({"error": f"Invalid database lookup format: {entity_id}"}), 400

    unified = build_unified_search_entity(entity_type, clean_id)
    if unified is None:
        return jsonify({"error": f"Entity contract mapping missing for: {entity_type} #{entity_id}"}), 404
    return jsonify(unified)


# ===========================================================================
# ROUTES — Admin (requires auth)
# ===========================================================================

@app.route("/admin", methods=["GET"])
@requires_auth
def admin():
    stats = _get_system_stats()
    with SessionLocal() as db:
        # Upload history from the registry (one row per file, richest data)
        upload_history = (
            db.query(UploadedFile)
            .order_by(UploadedFile.upload_time.desc())
            .limit(50)
            .all()
        )
        staging_data = (
            db.query(StagingData)
            .order_by(StagingData.created_at.desc())
            .limit(100)
            .all()
        )
        batch_summary = (
            db.query(
                StagingData.batch_id,
                StagingData.source_file,
                func.count(StagingData.id).label("total"),
                func.sum(
                    (StagingData.status == "PROCESSED").cast(_SAInteger)
                ).label("processed"),
                func.sum(
                    (StagingData.status == "FAILED").cast(_SAInteger)
                ).label("failed"),
                func.max(StagingData.created_at).label("timestamp"),
            )
            .group_by(StagingData.batch_id, StagingData.source_file)
            .order_by(func.max(StagingData.created_at).desc())
            .limit(20)
            .all()
        )
    return render_template(
        "admin.html",
        stats=stats,
        upload_history=upload_history,
        staging_data=staging_data,
        batch_summary=batch_summary,
    )


@app.route("/admin/ingest", methods=["POST"])
@requires_auth
def ingest():
    if "file" not in request.files:
        flash("No file part in request.", "danger")
        return redirect(url_for("admin"))

    file = request.files["file"]
    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin"))

    if not allowed_file(file.filename):
        flash(
            f"❌ Rejected '{file.filename}': only .xlsx and .xls files are accepted.",
            "danger",
        )
        logger.warning(f"Upload rejected — invalid extension: {file.filename}")
        return redirect(url_for("admin"))

    try:
        sha256_hash = hashlib.sha256()
        file.seek(0)
        for chunk in iter(lambda: file.read(4096), b""):
            sha256_hash.update(chunk)
        file_hash = sha256_hash.hexdigest()
        file.seek(0)  
    except Exception as hash_err:
        flash(f"❌ Failed to compute file hash: {hash_err}", "danger")
        logger.error(f"SHA-256 calculation error: {hash_err}")
        return redirect(url_for("admin"))

    with SessionLocal() as db:
        existing = db.query(UploadedFile).filter_by(file_hash=file_hash).first()
        if existing:
            # Duplicate detected — store context in session and let admin decide
            flask_session["dup_conflict"] = {
                "file_hash":     file_hash,
                "orig_name":     secure_filename(file.filename),
                "prev_filename": existing.filename,
                "prev_uploaded": str(existing.upload_time),
                "shop":          request.form.get("shop", "").strip(),
                "source_type":   request.form.get("source_type", "auto"),
                "upload_mode":   request.form.get("upload_mode", "upsert"),
                "filepath":      None,   # file not saved yet — filled below if we proceed
            }
            # Save file so it's available for reprocess action
            orig  = secure_filename(file.filename)
            stem, ext = os.path.splitext(orig)
            dup_path  = os.path.join(UPLOAD_FOLDER, f"{stem}_{_uuid.uuid4().hex[:8]}{ext}")
            file.save(dup_path)
            flask_session["dup_conflict"]["filepath"] = dup_path
            logger.info(f"Duplicate hash detected: '{file.filename}' — presenting options to admin.")
            return redirect(url_for("admin") + "?dup=1")

    original_name = secure_filename(file.filename)
    stem, ext = os.path.splitext(original_name)
    unique_name = f"{stem}_{_uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, unique_name)

    try:
        file.save(filepath)
    except Exception as save_err:
        flash(f"❌ Could not save file: {save_err}", "danger")
        logger.error(f"File save error: {save_err}")
        return redirect(url_for("admin"))

    try:
        import pandas as _pd
        _pd.read_excel(filepath, nrows=1)   
    except Exception as probe_err:
        if os.path.exists(filepath):
            os.remove(filepath)   
        flash(
            f"❌ '{original_name}' could not be read as an Excel workbook: {probe_err}",
            "danger",
        )
        logger.warning(f"Excel probe failed for '{original_name}': {probe_err}")
        return redirect(url_for("admin"))

    source_type  = request.form.get("source_type", "auto")
    shop         = request.form.get("shop", "").strip()
    upload_mode  = request.form.get("upload_mode", "upsert").strip()
    if upload_mode not in {"insert_only", "update_only", "upsert", "reprocess"}:
        upload_mode = "upsert"
    try:
        logger.info(f"Admin ingestion: file='{original_name}' type='{source_type}' shop='{shop or 'N/A'}' mode='{upload_mode}'")
        stats = IngestionPipeline.ingest_excel(filepath, source_type, shop=shop, upload_mode=upload_mode)
        logger.info(f"ETL stats: {stats}")

        mapper = KnowledgeMapper()
        map_stats = mapper.run()
        logger.info(f"Mapping stats: {map_stats}")

        index_count = SearchIndexer.rebuild_index()
        logger.info(f"Search index rebuilt: {index_count} documents")

        from graph_engine import rebuild_knowledge_graph
        graph_stats = rebuild_knowledge_graph()
        logger.info(f"Knowledge Graph sync: {graph_stats}")

        with SessionLocal() as db:
            db_file = UploadedFile(
                filename=original_name,
                file_hash=file_hash,
                shop_code=shop or None,
                uploaded_by=request.authorization.username if request.authorization else None,
                upload_mode=upload_mode,
                status="ok",
            )
            db.add(db_file)
            db.commit()

        flash(
            f"✅ '{original_name}' ingested ({upload_mode}). "
            f"Inserted: {stats.get('inserted', 0)} | Updated: {stats.get('updated', 0)} | "
            f"Skipped: {stats.get('skipped', 0)} | Errors: {stats.get('errors', 0)} | "
            f"Graph Nodes: {graph_stats['nodes']}",
            "success",
        )

        # Surface any missing schema columns as a non-blocking warning
        missing_cols = stats.get("missing_columns", [])
        if missing_cols:
            # Prefer user-friendly display names when available
            try:
                from data_engine import SHOP_DATA_SCHEMA, SHOP_DATA_DISPLAY
                disp_map = {k: v for k, v in zip(SHOP_DATA_SCHEMA, SHOP_DATA_DISPLAY)}
                display_cols = [disp_map.get(c, c) for c in missing_cols]
            except Exception:
                display_cols = missing_cols
            col_list = ", ".join(display_cols)
            flash(
                f"⚠️ Missing columns detected: {col_list}\nContinuing with available data.",
                "warning",
            )

    except ValueError as ve:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"⚠️ Validation error: {ve}", "danger")
        logger.warning(f"Metrology or format error for '{original_name}': {ve}")
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"❌ Ingestion pipeline failed: {e}", "danger")
        logger.error(f"Ingestion error for '{original_name}': {e}", exc_info=True)

    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Duplicate-file conflict resolution
# ---------------------------------------------------------------------------

@app.route("/admin/ingest/confirm", methods=["POST"])
@requires_auth
def ingest_confirm():
    """Handle admin choice when a duplicate file hash is detected."""
    conflict = flask_session.pop("dup_conflict", None)
    if not conflict:
        flash("⚠️ No conflict data found. Please upload the file again.", "warning")
        return redirect(url_for("admin"))

    action   = request.form.get("dup_action", "skip")   # skip | reprocess
    filepath = conflict["filepath"]
    orig_name   = conflict["orig_name"]
    shop        = conflict["shop"]
    source_type = conflict["source_type"]
    upload_mode = conflict.get("upload_mode", "upsert")
    if upload_mode not in {"insert_only", "update_only", "upsert", "reprocess"}:
        upload_mode = "upsert"

    # ── SKIP: discard the saved duplicate file, do nothing
    if action == "skip":
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        flash(f"✅ Upload skipped — existing data for '{orig_name}' unchanged.", "success")
        return redirect(url_for("admin"))

    # ── REPROCESS: re-run the pipeline on the (already saved) file
    if not filepath or not os.path.exists(filepath):
        flash(f"❌ Reprocess failed — file '{orig_name}' could not be found on disk.", "danger")
        return redirect(url_for("admin"))

    # Force upsert mode for reprocess so records are updated, not blocked
    effective_mode = upload_mode if upload_mode != "reprocess" else "upsert"

    try:
        username = request.authorization.username if request.authorization else None
        logger.info(f"Reprocessing duplicate: file='{orig_name}' mode='{effective_mode}' shop='{shop or 'N/A'}'")
        stats = IngestionPipeline.ingest_excel(filepath, source_type, shop=shop, upload_mode=effective_mode)

        mapper = KnowledgeMapper()
        map_stats = mapper.run()
        SearchIndexer.rebuild_index()
        from graph_engine import rebuild_knowledge_graph
        graph_stats = rebuild_knowledge_graph()

        # Log this reprocess event as a new audit row
        with SessionLocal() as db:
            db.add(UploadedFile(
                filename=orig_name,
                file_hash=conflict["file_hash"],
                shop_code=shop or None,
                uploaded_by=username,
                upload_mode=effective_mode,
                status="reprocessed",
            ))
            db.commit()

        flash(
            f"✅ '{orig_name}' reprocessed ({effective_mode}). "
            f"Inserted: {stats.get('inserted', 0)} | Updated: {stats.get('updated', 0)} | "
            f"Skipped: {stats.get('skipped', 0)} | Errors: {stats.get('errors', 0)} | "
            f"Graph Nodes: {graph_stats['nodes']}",
            "success",
        )
        missing_cols = stats.get("missing_columns", [])
        if missing_cols:
            try:
                from data_engine import SHOP_DATA_SCHEMA, SHOP_DATA_DISPLAY
                disp_map = {k: v for k, v in zip(SHOP_DATA_SCHEMA, SHOP_DATA_DISPLAY)}
                display_cols = [disp_map.get(c, c) for c in missing_cols]
            except Exception:
                display_cols = missing_cols
            flash(f"⚠️ Missing columns detected: {', '.join(display_cols)}\nContinuing with available data.", "warning")

    except Exception as e:
        flash(f"❌ Reprocess failed: {e}", "danger")
        logger.error(f"Reprocess error for '{orig_name}': {e}", exc_info=True)

    return redirect(url_for("admin"))


@app.route("/admin/rebuild-index", methods=["POST"])
@requires_auth
def rebuild_index():
    try:
        count = SearchIndexer.rebuild_index()
        from graph_engine import rebuild_knowledge_graph
        rebuild_knowledge_graph()
        flash(f"Search index and knowledge graph rebuilt — {count} documents indexed.", "success")
    except Exception as e:
        flash(f"Index rebuild failed: {e}", "danger")
    return redirect(url_for("admin"))


@app.route("/admin/remap", methods=["POST"])
@requires_auth
def remap():
    try:
        mapper = KnowledgeMapper()
        stats = mapper.run()
        from graph_engine import rebuild_knowledge_graph
        rebuild_knowledge_graph()
        flash(f"Knowledge mapping recomputed and graph synchronized — {stats}", "success")
    except Exception as e:
        flash(f"Mapping failed: {e}", "danger")
    return redirect(url_for("admin"))


@app.route("/admin/reset-db", methods=["POST"])
@requires_auth
def reset_db():
    try:
        with SessionLocal() as db:
            db.query(StationOperationMap).delete()
            db.query(SkillStationMap).delete()
            db.query(ToolStationMap).delete()
            db.query(SkillOperationMap).delete()
            db.query(TopicSkillMap).delete()
            db.query(CompetencyMap).delete()
            db.query(GraphRelationship).delete()
            db.query(GraphEntity).delete()
            db.query(StagingData).delete()
            db.query(UploadedFile).delete()
            
            from models import Subtopic, Topic, Semester, Diploma
            from models import Skill, Tool, Operation, Process, Station, Shop
            db.query(Subtopic).delete()
            db.query(Topic).delete()
            db.query(Semester).delete()
            db.query(Diploma).delete()
            db.query(Skill).delete()
            db.query(Tool).delete()
            db.query(Operation).delete()
            db.query(Process).delete()
            db.query(Station).delete()
            db.query(Shop).delete()
            db.commit()
            logger.info("Database domain reset completed.")

        re_ingested = 0
        for fname in os.listdir(UPLOAD_FOLDER):
            if fname.lower().endswith((".xlsx", ".xls")):
                fpath = os.path.join(UPLOAD_FOLDER, fname)
                try:
                    IngestionPipeline.ingest_excel(fpath)
                    re_ingested += 1
                except Exception as fe:
                    logger.error(f"Auto re-ingest failed for {fname}: {fe}")

        mapper = KnowledgeMapper()
        mapper.run()
        SearchIndexer.rebuild_index()
        from graph_engine import rebuild_knowledge_graph
        rebuild_knowledge_graph()

        flash(f"✅ Clean database reload finished. Processed {re_ingested} layout files.", "success")
    except Exception as e:
        flash(f"❌ Reset execution aborted: {e}", "danger")
        logger.error(f"DB master clean sequence failed: {e}", exc_info=True)
    return redirect(url_for("admin"))


# ===========================================================================
# ROUTES — WIS Document Upload (PowerPoint presentations)
# ===========================================================================

@app.route("/admin/upload-shop-wis", methods=["POST"])
@requires_auth
def upload_shop_wis():
    """Upload WIS (Work Instruction Set) PowerPoint document for a shop."""
    if "file" not in request.files:
        flash("No file part in request.", "danger")
        return redirect(url_for("admin"))

    file = request.files["file"]
    shop_code = request.form.get("shop_code", "").strip()

    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin"))

    if not allowed_wis_file(file.filename):
        flash(
            f"❌ Rejected '{file.filename}': only .ppt and .pptx files are accepted.",
            "danger",
        )
        logger.warning(f"WIS upload rejected — invalid extension: {file.filename}")
        return redirect(url_for("admin"))

    if not shop_code:
        flash("No shop selected.", "danger")
        return redirect(url_for("admin"))

    with SessionLocal() as db:
        shop = db.query(Shop).filter_by(shop_code=shop_code).first()
        if not shop:
            flash(f"Shop '{shop_code}' not found.", "danger")
            return redirect(url_for("admin"))

    # Create shop-specific directory
    wis_shop_dir = os.path.join(UPLOAD_FOLDER, "wis", "shops", shop.name)
    os.makedirs(wis_shop_dir, exist_ok=True)

    # Save file with UUID suffix
    original_name = secure_filename(file.filename)
    stem, ext = os.path.splitext(original_name)
    unique_name = f"{stem}_{_uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(wis_shop_dir, unique_name)

    try:
        file.save(filepath)
    except Exception as save_err:
        flash(f"❌ Could not save file: {save_err}", "danger")
        logger.error(f"File save error: {save_err}")
        return redirect(url_for("admin"))

    # Create database record
    try:
        with SessionLocal() as db:
            db_doc = ShopWISDocument(
                shop_id=shop.id,
                file_name=original_name,
                file_path=filepath,
                uploaded_by=request.authorization.username if request.authorization else None,
            )
            db.add(db_doc)
            db.commit()
            logger.info(f"Shop WIS document uploaded: shop_id={shop.id}, file='{original_name}'")
            flash(f"✅ WIS document '{original_name}' uploaded for shop '{shop.name}'.", "success")
    except Exception as db_err:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"❌ Could not save document record: {db_err}", "danger")
        logger.error(f"Database save error for shop WIS: {db_err}")

    return redirect(url_for("admin"))


@app.route("/admin/upload-station-wis", methods=["POST"])
@requires_auth
def upload_station_wis():
    """Upload WIS (Work Instruction Set) PowerPoint document for a station."""
    if "file" not in request.files:
        flash("No file part in request.", "danger")
        return redirect(url_for("admin"))

    file = request.files["file"]
    station_id_str = request.form.get("station_id", "").strip()

    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin"))

    if not allowed_wis_file(file.filename):
        flash(
            f"❌ Rejected '{file.filename}': only .ppt and .pptx files are accepted.",
            "danger",
        )
        logger.warning(f"WIS upload rejected — invalid extension: {file.filename}")
        return redirect(url_for("admin"))

    if not station_id_str:
        flash("No station selected.", "danger")
        return redirect(url_for("admin"))

    try:
        station_id = int(station_id_str)
    except (ValueError, TypeError):
        flash("Invalid station ID.", "danger")
        return redirect(url_for("admin"))

    with SessionLocal() as db:
        station = db.query(Station).filter_by(id=station_id).first()
        if not station:
            flash(f"Station ID {station_id} not found.", "danger")
            return redirect(url_for("admin"))

    # Create station-specific directory
    wis_station_dir = os.path.join(UPLOAD_FOLDER, "wis", "stations", station.station_code)
    os.makedirs(wis_station_dir, exist_ok=True)

    # Save file with UUID suffix
    original_name = secure_filename(file.filename)
    stem, ext = os.path.splitext(original_name)
    unique_name = f"{stem}_{_uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(wis_station_dir, unique_name)

    try:
        file.save(filepath)
    except Exception as save_err:
        flash(f"❌ Could not save file: {save_err}", "danger")
        logger.error(f"File save error: {save_err}")
        return redirect(url_for("admin"))

    # Create database record
    try:
        with SessionLocal() as db:
            db_doc = StationWISDocument(
                station_id=station_id,
                file_name=original_name,
                file_path=filepath,
                uploaded_by=request.authorization.username if request.authorization else None,
            )
            db.add(db_doc)
            db.commit()
            logger.info(f"Station WIS document uploaded: station_id={station_id}, file='{original_name}'")
            flash(f"✅ WIS document '{original_name}' uploaded for station '{station.station_code}'.", "success")
    except Exception as db_err:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"❌ Could not save document record: {db_err}", "danger")
        logger.error(f"Database save error for station WIS: {db_err}")

    return redirect(url_for("admin"))


# ===========================================================================
# ROUTES — WIS Document Retrieval & Download
# ===========================================================================

@app.route("/download-shop-wis/<int:doc_id>")
@requires_auth
def download_shop_wis(doc_id):
    """Download a shop-level WIS document."""
    with SessionLocal() as db:
        doc = db.query(ShopWISDocument).filter_by(id=doc_id).first()
        if not doc:
            flash("Document not found.", "danger")
            return redirect(url_for("admin"))
        
        if not os.path.exists(doc.file_path):
            flash("Document file not found on disk.", "danger")
            return redirect(url_for("admin"))
        
        try:
            return send_file(
                doc.file_path,
                as_attachment=True,
                download_name=doc.file_name,
                mimetype="application/octet-stream"
            )
        except Exception as e:
            logger.error(f"Download failed for doc {doc_id}: {e}")
            flash(f"Download failed: {e}", "danger")
            return redirect(url_for("admin"))


@app.route("/view-shop-wis/<int:doc_id>")
def view_shop_wis(doc_id):
    """View a shop-level WIS document in browser (for PDF/media)."""
    with SessionLocal() as db:
        doc = db.query(ShopWISDocument).filter_by(id=doc_id).first()
        if not doc:
            return jsonify({"error": "Document not found"}), 404
        
        if not os.path.exists(doc.file_path):
            return jsonify({"error": "Document file not found"}), 404
        
        try:
            ext = os.path.splitext(doc.file_name.lower())[1]
            mimetype_map = {
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".ppt": "application/vnd.ms-powerpoint",
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }
            mimetype = mimetype_map.get(ext, "application/octet-stream")
            return send_file(doc.file_path, mimetype=mimetype)
        except Exception as e:
            logger.error(f"View failed for doc {doc_id}: {e}")
            return jsonify({"error": "View failed"}), 500


@app.route("/download-station-wis/<int:doc_id>")
@requires_auth
def download_station_wis(doc_id):
    """Download a station-level WIS document."""
    with SessionLocal() as db:
        doc = db.query(StationWISDocument).filter_by(id=doc_id).first()
        if not doc:
            flash("Document not found.", "danger")
            return redirect(url_for("admin"))
        
        if not os.path.exists(doc.file_path):
            flash("Document file not found on disk.", "danger")
            return redirect(url_for("admin"))
        
        try:
            return send_file(
                doc.file_path,
                as_attachment=True,
                download_name=doc.file_name,
                mimetype="application/octet-stream"
            )
        except Exception as e:
            logger.error(f"Download failed for doc {doc_id}: {e}")
            flash(f"Download failed: {e}", "danger")
            return redirect(url_for("admin"))


@app.route("/view-station-wis/<int:doc_id>")
def view_station_wis(doc_id):
    """View a station-level WIS document in browser (for PDF/media)."""
    with SessionLocal() as db:
        doc = db.query(StationWISDocument).filter_by(id=doc_id).first()
        if not doc:
            return jsonify({"error": "Document not found"}), 404
        
        if not os.path.exists(doc.file_path):
            return jsonify({"error": "Document file not found"}), 404
        
        try:
            ext = os.path.splitext(doc.file_name.lower())[1]
            mimetype_map = {
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".ppt": "application/vnd.ms-powerpoint",
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }
            mimetype = mimetype_map.get(ext, "application/octet-stream")
            return send_file(doc.file_path, mimetype=mimetype)
        except Exception as e:
            logger.error(f"View failed for doc {doc_id}: {e}")
            return jsonify({"error": "View failed"}), 500


# ===========================================================================
# ROUTES — WIS Document Viewer Pages
# ===========================================================================

@app.route("/shop/wis/<int:doc_id>")
def shop_wis_viewer(doc_id):
    """Display shop WIS document viewer page with metadata."""
    with SessionLocal() as db:
        doc = db.query(ShopWISDocument).filter_by(id=doc_id).first()
        if not doc:
            flash("WIS document not found.", "danger")
            return redirect(url_for("index"))
        
        shop = doc.shop
        return render_template(
            "wis-viewer.html",
            doc_id=doc.id,
            file_name=doc.file_name,
            uploaded_at=doc.uploaded_at,
            uploaded_by=doc.uploaded_by,
            entity_type="shop",
            entity_name=shop.name,
            entity_code=shop.shop_code,
            view_url=url_for("view_shop_wis", doc_id=doc.id),
            download_url=url_for("download_shop_wis", doc_id=doc.id),
        )


@app.route("/station/wis/<int:doc_id>")
def station_wis_viewer(doc_id):
    """Display station WIS document viewer page with metadata."""
    with SessionLocal() as db:
        doc = db.query(StationWISDocument).filter_by(id=doc_id).first()
        if not doc:
            flash("WIS document not found.", "danger")
            return redirect(url_for("index"))
        
        station = doc.station
        shop = station.shop
        return render_template(
            "wis-viewer.html",
            doc_id=doc.id,
            file_name=doc.file_name,
            uploaded_at=doc.uploaded_at,
            uploaded_by=doc.uploaded_by,
            entity_type="station",
            entity_name=station.station_code,
            entity_code=station.station_code,
            station_id=station.id,
            shop_code=shop.shop_code if shop else "N/A",
            shop_name=shop.name if shop else "N/A",
            view_url=url_for("view_station_wis", doc_id=doc.id),
            download_url=url_for("download_station_wis", doc_id=doc.id),
        )


def _wal_checkpoint():
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            logger.info("WAL checkpoint finalized safely on shutdown sequence.")
    except Exception as e:
        logger.warning(f"WAL checkpoint failure: {e}")



atexit.register(_wal_checkpoint)

if __name__ == "__main__":
    init_db()
    logger.info(f"Starting IIK-CME production server on port 5000. DEBUG={DEBUG_MODE}")
    app.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE, use_reloader=DEBUG_MODE)
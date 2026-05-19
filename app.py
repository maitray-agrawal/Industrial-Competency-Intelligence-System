"""
app.py
------
Flask application for IIK-CME.
Production-safe configuration with env-var debug toggle.
"""

import os
import atexit
import hashlib
import uuid as _uuid
from flask import Flask, render_template, request, Response, redirect, url_for, flash, jsonify

from werkzeug.utils import secure_filename

from database import SessionLocal, engine, init_db
from models import (
    Shop, Station, Process, Operation, Skill, Tool,
    Topic, Subtopic, StagingData, CompetencyMap,
    SkillOperationMap, TopicSkillMap, ToolStationMap, UploadedFile
)
from sqlalchemy import Integer as _SAInteger
from search_engine import SearchAPI, SearchIndexer
from data_engine import IngestionPipeline
from heuristic_engine import KnowledgeMapper
from competency_engine import CompetencyEngine
from logger import get_logger
from sqlalchemy import text, func

logger = get_logger("FlaskApp")

# ---------------------------------------------------------------------------
# App Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("IIK_SECRET_KEY", "industrial-offline-secret-2025")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {".xlsx", ".xls"}

DEBUG_MODE = os.environ.get("FLASK_ENV", "production").lower() == "development"


def allowed_file(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Auth — HTTP Basic Auth for admin panel
# ---------------------------------------------------------------------------
ADMIN_USER = os.environ.get("IIK_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("IIK_ADMIN_PASS", "secure_offline_123")


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
# Error Handlers — prevents debugger page in production
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
    with SessionLocal() as session:
        return {
            "shops":      session.query(Shop).count(),
            "stations":   session.query(Station).count(),
            "processes":  session.query(Process).count(),
            "operations": session.query(Operation).count(),
            "skills":     session.query(Skill).count(),
            "tools":      session.query(Tool).count(),
            "topics":     session.query(Topic).count(),
            "subtopics":  session.query(Subtopic).count(),
            "mappings":   session.query(CompetencyMap).count(),
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
    return render_template("station.html", profile=profile)


@app.route("/graph")
def graph():
    return render_template("graph.html")


# ===========================================================================
# ROUTES — API  (JSON endpoints for graph and competency dashboard)
# ===========================================================================

@app.route("/api/graph-data")
def api_graph_data():
    """Return nodes and edges for the knowledge graph visualization."""
    nodes = []
    edges = []
    node_id = 0

    # Colour palette per entity type
    COLOR = {
        "shop":      "#3b82f6",  # blue
        "station":   "#06b6d4",  # cyan
        "process":   "#10b981",  # green
        "operation": "#f59e0b",  # amber
        "skill":     "#f97316",  # orange
        "tool":      "#ef4444",  # red
        "topic":     "#a855f7",  # purple
        "subtopic":  "#ec4899",  # pink
    }

    entity_node_map: dict[str, int] = {}  # "TYPE_id" → vis node id

    def add_node(label: str, group: str, title: str) -> int:
        nonlocal node_id
        nid = node_id
        nodes.append({
            "id":    nid,
            "label": label[:30],
            "title": title,
            "group": group,
            "color": COLOR.get(group, "#94a3b8"),
        })
        node_id += 1
        return nid

    with SessionLocal() as session:
        # Shops
        for obj in session.query(Shop).all():
            key = f"shop_{obj.id}"
            entity_node_map[key] = add_node(obj.shop_code, "shop", obj.name)

        # Stations + edges to Shop
        for obj in session.query(Station).all():
            key = f"station_{obj.id}"
            nid = add_node(obj.station_code, "station", obj.name)
            entity_node_map[key] = nid
            shop_key = f"shop_{obj.shop_id}"
            if shop_key in entity_node_map:
                edges.append({
                    "from": entity_node_map[shop_key],
                    "to":   nid,
                    "label": "has_station",
                })

        # Processes → Station
        for obj in session.query(Process).all():
            key = f"process_{obj.id}"
            nid = add_node(obj.name[:20], "process", obj.name)
            entity_node_map[key] = nid
            stn_key = f"station_{obj.station_id}"
            if stn_key in entity_node_map:
                edges.append({
                    "from": entity_node_map[stn_key],
                    "to":   nid,
                    "label": "has_process",
                })

        # Operations → Process
        for obj in session.query(Operation).limit(200).all():  # cap for performance
            key = f"op_{obj.id}"
            nid = add_node(obj.name[:20], "operation", obj.name)
            entity_node_map[key] = nid
            proc_key = f"process_{obj.process_id}"
            if proc_key in entity_node_map:
                edges.append({
                    "from": entity_node_map[proc_key],
                    "to":   nid,
                    "label": "has_operation",
                })

        # Skills
        for obj in session.query(Skill).all():
            key = f"skill_{obj.id}"
            entity_node_map[key] = add_node(obj.name[:20], "skill", obj.name)

        # Skill ↔ Operation edges
        for link in session.query(SkillOperationMap).all():
            sk_key = f"skill_{link.skill_id}"
            op_key = f"op_{link.operation_id}"
            if sk_key in entity_node_map and op_key in entity_node_map:
                edges.append({
                    "from":  entity_node_map[sk_key],
                    "to":    entity_node_map[op_key],
                    "label": f"requires ({round(link.confidence, 2)})",
                    "dashes": True,
                })

        # Tools
        for link in session.query(ToolStationMap).all():
            tool = session.get(Tool, link.tool_id)
            stn_key = f"station_{link.station_id}"
            if tool and stn_key in entity_node_map:
                tool_key = f"tool_{tool.id}"
                if tool_key not in entity_node_map:
                    entity_node_map[tool_key] = add_node(tool.name[:20], "tool", tool.name)
                edges.append({
                    "from":  entity_node_map[stn_key],
                    "to":    entity_node_map[tool_key],
                    "label": "uses_tool",
                })

        # Topics
        for obj in session.query(Topic).all():
            key = f"topic_{obj.id}"
            entity_node_map[key] = add_node(obj.title[:20], "topic", obj.title)

        # Topic ↔ Skill edges
        for link in session.query(TopicSkillMap).all():
            t_key = f"topic_{link.topic_id}"
            s_key = f"skill_{link.skill_id}"
            if t_key in entity_node_map and s_key in entity_node_map:
                edges.append({
                    "from":  entity_node_map[t_key],
                    "to":    entity_node_map[s_key],
                    "label": f"covers ({round(link.confidence, 2)})",
                    "color": "#a855f7",
                })

    return jsonify({"nodes": nodes, "edges": edges})


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


# ===========================================================================
# ROUTES — Admin (requires auth)
# ===========================================================================

@app.route("/admin", methods=["GET"])
@requires_auth
def admin():
    stats = _get_system_stats()
    with SessionLocal() as session:
        staging_data = (
            session.query(StagingData)
            .order_by(StagingData.created_at.desc())
            .limit(100)
            .all()
        )
        # Batch summary for audit view
        # NOTE: .cast() requires a SQLAlchemy type, NOT a Python builtin.
        batch_summary = (
            session.query(
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
        staging_data=staging_data,
        batch_summary=batch_summary,
    )


@app.route("/admin/ingest", methods=["POST"])
@requires_auth
def ingest():
    # ── 1. Presence check ──────────────────────────────────────────────────
    if "file" not in request.files:
        flash("No file part in request.", "danger")
        return redirect(url_for("admin"))

    file = request.files["file"]
    if not file or not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin"))

    # ── 2. Extension whitelist (BEFORE touching disk) ──────────────────────
    if not allowed_file(file.filename):
        flash(
            f"❌ Rejected '{file.filename}': only .xlsx and .xls files are accepted.",
            "danger",
        )
        logger.warning(f"Upload rejected — invalid extension: {file.filename}")
        return redirect(url_for("admin"))

    # ── 3. Calculate SHA-256 of file stream to prevent duplicate ingestion ─
    try:
        sha256_hash = hashlib.sha256()
        file.seek(0)
        for chunk in iter(lambda: file.read(4096), b""):
            sha256_hash.update(chunk)
        file_hash = sha256_hash.hexdigest()
        file.seek(0)  # reset pointer for saving
    except Exception as hash_err:
        flash(f"❌ Failed to compute file hash: {hash_err}", "danger")
        logger.error(f"SHA-256 calculation error: {hash_err}")
        return redirect(url_for("admin"))

    # Verify duplicate hash in registry
    with SessionLocal() as session:
        existing = session.query(UploadedFile).filter_by(file_hash=file_hash).first()
        if existing:
            flash(f"⚠️ File already exists in system. (Hash matches previously uploaded '{existing.filename}')", "warning")
            logger.info(f"Duplicate upload blocked: '{file.filename}' matches '{existing.filename}'")
            return redirect(url_for("admin"))

    # ── 4. Sanitize filename + make unique to prevent overwrites ───────────
    original_name = secure_filename(file.filename)
    stem, ext = os.path.splitext(original_name)
    unique_name = f"{stem}_{_uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, unique_name)

    # ── 5. Save to upload folder ───────────────────────────────────────────
    try:
        file.save(filepath)
    except Exception as save_err:
        flash(f"❌ Could not save file: {save_err}", "danger")
        logger.error(f"File save error: {save_err}")
        return redirect(url_for("admin"))

    # ── 6. Validate that the file is a readable Excel workbook ────────────
    try:
        import pandas as _pd
        _pd.read_excel(filepath, nrows=1)   # lightweight probe — just read header row
    except Exception as probe_err:
        if os.path.exists(filepath):
            os.remove(filepath)   # discard corrupt / non-Excel file
        flash(
            f"❌ '{original_name}' could not be read as an Excel workbook: {probe_err}",
            "danger",
        )
        logger.warning(f"Excel probe failed for '{original_name}': {probe_err}")
        return redirect(url_for("admin"))

    # ── 7. Run ETL pipeline ────────────────────────────────────────────────
    source_type = request.form.get("source_type", "auto")
    try:
        logger.info(f"Admin ingestion: file='{original_name}' type='{source_type}'")
        stats = IngestionPipeline.ingest_excel(filepath, source_type)
        logger.info(f"ETL stats: {stats}")

        # ── 8. Recompute mappings ──────────────────────────────────────────
        mapper = KnowledgeMapper()
        map_stats = mapper.run()
        logger.info(f"Mapping stats: {map_stats}")

        # ── 9. Rebuild FTS5 search index ──────────────────────────────────
        index_count = SearchIndexer.rebuild_index()
        logger.info(f"Search index rebuilt: {index_count} documents")

        # ── 10. Rebuild Knowledge Graph Cache ──────────────────────────────
        from graph_engine import rebuild_knowledge_graph
        graph_stats = rebuild_knowledge_graph()
        logger.info(f"Knowledge Graph sync: {graph_stats}")

        # ── 11. Add to Uploaded Registry ───────────────────────────────────
        with SessionLocal() as session:
            db_file = UploadedFile(
                filename=original_name,
                file_hash=file_hash
            )
            session.add(db_file)
            session.commit()

        flash(
            f"✅ '{original_name}' ingested successfully! "
            f"ETL: {stats} | Mappings: {map_stats} | Graph Nodes: {graph_stats['nodes']}",
            "success",
        )

    except ValueError as ve:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"⚠️ Validation error: {ve}", "danger")
        logger.warning(f"Ingestion validation error for '{original_name}': {ve}")
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f"❌ Ingestion pipeline failed: {e}", "danger")
        logger.error(f"Ingestion error for '{original_name}': {e}", exc_info=True)

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


# ===========================================================================
# NEW API ENDPOINTS — ADVANCED SEARCH FLOW & CARD EXPANSIONS
# ===========================================================================

@app.route("/api/search-expand")
def api_search_expand():
    """
    Run FTS5 search then recursively expand neighbors (depth 2)
    to return a connected Vis.js compatible graph for search query.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"direct_matches": [], "graph": {"nodes": [], "edges": []}})

    from graph_engine import GraphRelationshipEngine
    data = GraphRelationshipEngine.search_and_expand_ecosystem(q)
    return jsonify(data)


@app.route("/api/entity-details/<string:entity_type>/<int:entity_id>")
def api_entity_details(entity_type, entity_id):
    """
    Returns full detailed connectivity profile of an entity for clickable expandable cards.
    """
    from graph_engine import GraphRelationshipEngine
    profile = GraphRelationshipEngine.get_expanded_card_details(entity_type, entity_id)
    return jsonify(profile)


# ===========================================================================
# WAL CHECKPOINT ON CLEAN SHUTDOWN
# ===========================================================================
def _wal_checkpoint():
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            logger.info("WAL checkpoint completed on shutdown.")
    except Exception as e:
        logger.warning(f"WAL checkpoint failed: {e}")


atexit.register(_wal_checkpoint)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    init_db()
    logger.info(f"Starting IIK-CME server. DEBUG={DEBUG_MODE}")
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=DEBUG_MODE,
        use_reloader=DEBUG_MODE,
    )

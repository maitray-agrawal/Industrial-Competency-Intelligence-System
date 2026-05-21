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
    SkillOperationMap, TopicSkillMap, ToolStationMap, UploadedFile,
    SkillStationMap, StationOperationMap,
    GraphEntity, GraphRelationship
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

    # Colour palette per entity type
    COLOR = {
        "shop":      "#3b82f6",  # blue
        "station":   "#06b6d4",  # cyan
        "process":   "#10b981",  # green
        "operation": "#f59e0b",  # amber
        "skill":     "#f97316",  # orange
        "tool":      "#ef4444",  # red
        "subject":   "#a855f7",  # purple
        "topic":     "#ec4899",  # pink
        "semester":  "#64748b",  # slate
        "diploma":   "#475569",  # slate-dark
    }

    with SessionLocal() as session:
        db_nodes = session.query(GraphEntity).all()
        for ent in db_nodes:
            nodes.append({
                "id":    ent.id,
                "label": ent.name[:30] + ("..." if len(ent.name) > 30 else ""),
                "title": f"[{ent.entity_type.upper()}] {ent.name}",
                "group": ent.entity_type.lower(),
                "color": COLOR.get(ent.entity_type.lower(), "#94a3b8")
            })

        db_relationships = session.query(GraphRelationship).all()
        for rel in db_relationships:
            edges.append({
                "from":  rel.source_id,
                "to":    rel.target_id,
                "label": f"{rel.rel_type} ({round(rel.weight, 2)})"
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


@app.route("/admin/reset-db", methods=["POST"])
@requires_auth
def reset_db():
    """
    Wipes all industrial domain tables and re-runs ETL from the last uploaded station file.
    Use this after fixing ETL logic to regenerate clean, correct station data.
    """
    try:
        from database import Base, engine as _engine
        import sqlalchemy

        with SessionLocal() as session:
            # Delete all domain data in dependency order (leaves → roots)
            session.query(StationOperationMap).delete()
            session.query(SkillStationMap).delete()
            session.query(ToolStationMap).delete()
            session.query(SkillOperationMap).delete()
            session.query(CompetencyMap).delete()
            session.query(GraphRelationship).delete()
            session.query(GraphEntity).delete()
            session.query(StagingData).delete()
            session.query(UploadedFile).delete()
            # Domain entities
            from models import Subtopic, Topic, Semester, Diploma
            from models import Skill, Tool, Operation, Process, Station, Shop
            session.query(Subtopic).delete()
            session.query(Topic).delete()
            session.query(Semester).delete()
            session.query(Diploma).delete()
            session.query(Skill).delete()
            session.query(Tool).delete()
            session.query(Operation).delete()
            session.query(Process).delete()
            session.query(Station).delete()
            session.query(Shop).delete()
            session.commit()
            logger.info("Database reset complete.")

        # Re-run on every xlsx in uploads folder
        re_ingested = 0
        for fname in os.listdir(UPLOAD_FOLDER):
            if fname.lower().endswith((".xlsx", ".xls")):
                fpath = os.path.join(UPLOAD_FOLDER, fname)
                try:
                    stats = IngestionPipeline.ingest_excel(fpath)
                    mapper = KnowledgeMapper()
                    mapper.run()
                    SearchIndexer.rebuild_index()
                    from graph_engine import rebuild_knowledge_graph
                    rebuild_knowledge_graph()
                    re_ingested += 1
                    logger.info(f"Re-ingested: {fname} — {stats}")
                except Exception as fe:
                    logger.error(f"Re-ingest failed for {fname}: {fe}", exc_info=True)

        flash(
            f"✅ Database reset and re-ingested {re_ingested} file(s). "
            "Station IDs, tools, and mappings are now correct.",
            "success"
        )
    except Exception as e:
        flash(f"❌ Reset failed: {e}", "danger")
        logger.error(f"DB reset failed: {e}", exc_info=True)
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

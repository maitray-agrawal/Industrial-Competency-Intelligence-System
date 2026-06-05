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

DEBUG_MODE = os.environ.get("FLASK_ENV", "production").lower() == "development"

def allowed_file(filename: str) -> bool:
    return os.path.splitext(filename.lower()) in ALLOWED_EXTENSIONS or os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


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
    stats = _get_system_stats()
    return render_template("graph.html", stats=stats)

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
    with SessionLocal() as session:
        staging_data = (
            session.query(StagingData)
            .order_by(StagingData.created_at.desc())
            .limit(100)
            .all()
        )
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

    with SessionLocal() as session:
        existing = session.query(UploadedFile).filter_by(file_hash=file_hash).first()
        if existing:
            flash(f"⚠️ File already exists in system. (Hash matches previously uploaded '{existing.filename}')", "warning")
            logger.info(f"Duplicate upload blocked: '{file.filename}' matches '{existing.filename}'")
            return redirect(url_for("admin"))

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

    source_type = request.form.get("source_type", "auto")
    try:
        logger.info(f"Admin ingestion: file='{original_name}' type='{source_type}'")
        stats = IngestionPipeline.ingest_excel(filepath, source_type)
        logger.info(f"ETL stats: {stats}")

        mapper = KnowledgeMapper()
        map_stats = mapper.run()
        logger.info(f"Mapping stats: {map_stats}")

        index_count = SearchIndexer.rebuild_index()
        logger.info(f"Search index rebuilt: {index_count} documents")

        from graph_engine import rebuild_knowledge_graph
        graph_stats = rebuild_knowledge_graph()
        logger.info(f"Knowledge Graph sync: {graph_stats}")

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
        logger.warning(f"Metrology or format error for '{original_name}': {ve}")
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
    try:
        with SessionLocal() as session:
            session.query(StationOperationMap).delete()
            session.query(SkillStationMap).delete()
            session.query(ToolStationMap).delete()
            session.query(SkillOperationMap).delete()
            session.query(CompetencyMap).delete()
            session.query(GraphRelationship).delete()
            session.query(GraphEntity).delete()
            session.query(StagingData).delete()
            session.query(UploadedFile).delete()
            
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
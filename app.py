import os
from functools import wraps
from flask import Flask, render_template, request, Response, redirect, url_for, flash
from werkzeug.utils import secure_filename
from database import SessionLocal
from models import Trade, Workstation, MappingEngine, StagingData
from search_engine import SearchAPI, SearchIndexer
from data_engine import IngestionPipeline, ValidationEngine, NormalizationEngine
from heuristic_engine import MappingCalculator
from logger import get_logger

logger = get_logger("FlaskApp")

app = Flask(__name__)
app.secret_key = "industrial-offline-secret"
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Basic Auth logic for Admin
def check_auth(username, password):
    return username == 'admin' and password == 'secure_offline_123'

def authenticate():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def index():
    # Render dashboard showing trades and mapped workstations
    with SessionLocal() as session:
        trades = session.query(Trade).all()
        # Complex query to get mappings with names
        mappings = session.query(
            MappingEngine.relevance_score,
            Trade.name.label('trade_name'),
            Workstation.workstation_code
        ).join(Trade, MappingEngine.trade_id == Trade.id)\
         .join(Workstation, MappingEngine.workstation_id == Workstation.id)\
         .order_by(MappingEngine.relevance_score.desc()).all()
        
    return render_template("index.html", trades=trades, mappings=mappings)

@app.route("/search")
def search():
    q = request.args.get("q", "")
    results = []
    if q:
        results = SearchAPI.search(q)
    return render_template("index.html", search_results=results, query=q)

@app.route("/admin", methods=["GET"])
@requires_auth
def admin():
    with SessionLocal() as session:
        staging_data = session.query(StagingData).order_by(StagingData.created_at.desc()).limit(50).all()
    return render_template("admin.html", staging_data=staging_data)

@app.route("/admin/ingest", methods=["POST"])
@requires_auth
def ingest():
    if 'file' not in request.files:
        flash("No file part", "danger")
        return redirect(url_for('admin'))
        
    file = request.files['file']
    source_type = request.form.get("source_type")
    
    if file.filename == '':
        flash("No selected file", "danger")
        return redirect(url_for('admin'))
        
    if file and source_type:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        try:
            logger.info(f"Admin triggered upload for {filename} as {source_type}")
            # Full Pipeline Execution
            IngestionPipeline.ingest_csv(filepath, source_type)
            ValidationEngine.validate_pending_records()
            NormalizationEngine.process_validated_records()
            
            # Recompute mappings and search index deterministically
            calc = MappingCalculator()
            calc.compute_all_mappings()
            SearchIndexer.rebuild_index()
            
            flash(f"Successfully processed {filename} as {source_type} and updated mappings/search index.", "success")
        except Exception as e:
            logger.error(f"Ingestion failed: {str(e)}")
            flash(f"Error processing file: {str(e)}", "danger")
            
    return redirect(url_for('admin'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

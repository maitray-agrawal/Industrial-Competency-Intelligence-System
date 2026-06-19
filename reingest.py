"""
reingest.py
-----------
One-shot script to wipe and re-ingest all uploaded data with corrected ETL logic.
Run from the project root: python reingest.py
"""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

print("=" * 60)
print("IIK-CME   Re-Ingest Script")
print("=" * 60)

from database import init_db, SessionLocal
init_db()
print("[OK] DB schema initialized / verified.")

from data_engine import IngestionPipeline
from heuristic_engine import KnowledgeMapper
from search_engine import SearchIndexer
from graph_engine import rebuild_knowledge_graph

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

# Dynamically discover shop data files — any .xlsx/.xls in uploads that are not TCF syllabus files
shop_data_files = [
    f for f in os.listdir(UPLOAD_DIR)
    if f.lower().endswith((".xlsx", ".xls"))
    and not f.upper().startswith("TCF")
]
tcf_files = [
    f for f in os.listdir(UPLOAD_DIR)
    if f.lower().endswith((".xlsx", ".xls"))
    and f.upper().startswith("TCF")
]

for fname in shop_data_files:
    fpath = os.path.join(UPLOAD_DIR, fname)
    if os.path.exists(fpath):
        print(f"\n[INGEST] Shop data: {fname}")
        stats = IngestionPipeline.ingest_excel(fpath, source_type="station_data")
        print(f"   ETL Stats: {stats}")
    else:
        print(f"[SKIP] Not found: {fpath}")

for fname in tcf_files:
    fpath = os.path.join(UPLOAD_DIR, fname)
    if os.path.exists(fpath):
        print(f"\n[INGEST] TCF Syllabus: {fname}")
        stats = IngestionPipeline.ingest_excel(fpath, source_type="tcf_data")
        print(f"   ETL Stats: {stats}")
    else:
        print(f"[SKIP] Not found: {fpath}")

print("\n[MAPPER] Computing knowledge mappings...")
mapper = KnowledgeMapper()
mstats = mapper.run()
print(f"   Mapper stats: {mstats}")

print("\n[INDEX] Rebuilding FTS5 search index...")
n = SearchIndexer.rebuild_index()
print(f"   Indexed: {n} documents")

print("\n[GRAPH] Rebuilding knowledge graph...")
gstats = rebuild_knowledge_graph()
nodes = gstats.get("nodes", 0)
edges = gstats.get("edges", 0)
print(f"   Graph: {nodes} nodes, {edges} edges")

print("\n[VERIFY] Station data quality check:")
from models import Station, Tool, ToolStationMap, SkillStationMap
with SessionLocal() as session:
    stations = session.query(Station).all()
    print(f"   Total stations: {len(stations)}")
    for st in stations[:8]:
        tool_names  = [tl.tool.name for tl in st.tool_links]
        skill_names = [sk.skill.name for sk in st.skill_links]
        print(f"\n   Station: raw_station_id={st.raw_station_id!r}   name={st.name!r}")
        print(f"     station_code = {st.station_code!r}")
        print(f"     Tools  ({len(tool_names)}): {tool_names[:5]}")
        print(f"     Skills ({len(skill_names)}): {skill_names[:3]}")

print("\n" + "=" * 60)
print("Re-ingest complete! Start the server with:  python app.py")
print("=" * 60)

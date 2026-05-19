from database import init_db, SessionLocal
from models import Tool, Workstation
from search_engine import SearchIndexer, SearchAPI

def seed_tools():
    with SessionLocal() as session:
        ws = session.query(Workstation).filter_by(workstation_code="WS-CNC-01").first()
        if not ws:
            return
            
        t1 = Tool(tool_name="Carbide End Mill", description="1/2 inch 4-flute solid carbide end mill for heavy metal removal.", workstation_id=ws.id)
        t2 = Tool(tool_name="Coolant Pump", description="High pressure flood coolant system.", workstation_id=ws.id)
        
        # Avoid duplicate insert
        if not session.query(Tool).filter_by(tool_name="Carbide End Mill").first():
            session.add_all([t1, t2])
            session.commit()

def test_fts5():
    # 1. Initialize and Seed
    init_db()
    seed_tools()
    
    # 2. Rebuild FTS5 Index
    SearchIndexer.rebuild_index()
    
    # 3. Test Queries
    queries = [
        "carbide",
        "welding OR safely",
        "metal properties"
    ]
    
    for q in queries:
        print(f"\n--- Results for: {q} ---")
        results = SearchAPI.search(q)
        for r in results:
            print(f"[{r['entity_type']}] {r['title']}")
            print(f"    Snippet: {r['highlight']}")
            print(f"    Rank Score: {r['rank']:.4f}")

if __name__ == "__main__":
    test_fts5()

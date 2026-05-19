from database import init_db, SessionLocal
from models import Trade, Workstation, Skill, AcademicTheory, MappingEngine
from heuristic_engine import MappingCalculator

def seed_mock_data():
    with SessionLocal() as session:
        # Check if already seeded
        if session.query(Trade).first():
            return
            
        # Seed Trade
        t1 = Trade(name="Machinist", description="Operates machine tools to produce precision metal parts.")
        t2 = Trade(name="Welder", description="Joins metal parts using various welding techniques and tools.")
        session.add_all([t1, t2])
        session.commit()
        
        # Seed Workstation
        w1 = Workstation(workstation_code="WS-CNC-01", description="CNC Milling Center for precision parts.", trade_id=t1.id)
        w2 = Workstation(workstation_code="WS-WELD-01", description="TIG Welding Bay.", trade_id=t2.id)
        session.add_all([w1, w2])
        session.commit()
        
        # Seed Skill
        s1 = Skill(skill_code="SK-CNC-OP", name="CNC Operation", description="Operating CNC milling machines safely and precisely.", workstation_id=w1.id)
        s2 = Skill(skill_code="SK-TIG", name="TIG Welding", description="Performing TIG welds on steel and aluminum.", workstation_id=w2.id)
        session.add_all([s1, s2])
        session.commit()
        
        # Seed Theory
        th1 = AcademicTheory(module_code="TH-MET-01", title="Metallurgy Basics", content="Understanding metal properties for machining.", skill_id=s1.id)
        th2 = AcademicTheory(module_code="TH-WLD-01", title="Welding Safety", content="Safety protocols for TIG and MIG welding.", skill_id=s2.id)
        session.add_all([th1, th2])
        session.commit()
        
def test_mapping():
    calculator = MappingCalculator()
    calculator.compute_all_mappings()
    
    with SessionLocal() as session:
        mappings = session.query(MappingEngine).all()
        for m in mappings:
            print(f"Trade ID: {m.trade_id} | Workstation ID: {m.workstation_id} | Relevance: {m.relevance_score:.4f}")

if __name__ == "__main__":
    init_db()
    seed_mock_data()
    test_mapping()

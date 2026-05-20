"""
graph_engine.py
---------------
Unified Industrial Knowledge Graph Engine for IIK-CME.
Handles:
  1. Synchronizing all domain records to unified `entities` and `relationships` tables.
  2. Traversal traversal algorithms (BFS/DFS, weighted path connections).
  3. Interactive dynamic details fetching for clickable expandable cards.
"""

from collections import deque
from sqlalchemy.orm import Session
from database import SessionLocal
from models import (
    Shop, Station, Process, Operation, Skill, Tool,
    Diploma, Semester, Topic, Subtopic,
    SkillOperationMap, ToolStationMap, TopicSkillMap, CompetencyMap,
    GraphEntity, GraphRelationship, EntityType, MappingScore
)
from logger import get_logger

logger = get_logger("GraphEngine")

# Entity Type registers
ENTITY_TYPES = ["shop", "station", "process", "operation", "skill", "tool", "topic", "subject", "semester", "diploma"]


def rebuild_knowledge_graph() -> dict:
    """
    Cleans and rebuilds the unified graph cache ('entities' and 'relationships' tables).
    Establishes all direct, reverse, and inferred edges.
    """
    logger.info("Starting Knowledge Graph synchronisation and expansion...")
    stats = {"nodes": 0, "edges": 0}

    with SessionLocal() as session:
        try:
            # 1. Clean existing graph tables
            session.query(GraphRelationship).delete()
            session.query(GraphEntity).delete()
            session.query(EntityType).delete()
            session.flush()

            # Seed entity types
            for t in ENTITY_TYPES:
                session.add(EntityType(name=t))
            session.flush()

            # Keep trace of registered code -> id
            code_id_map: dict[str, int] = {}

            # Helper to add entity
            def add_entity(etype: str, code: str, name: str, props: dict = None) -> int:
                code_normalized = str(code).strip().upper()
                if code_normalized in code_id_map:
                    return code_id_map[code_normalized]

                ent = GraphEntity(
                    entity_type=etype,
                    code=code_normalized,
                    name=name,
                    properties=props or {}
                )
                session.add(ent)
                session.flush()
                code_id_map[code_normalized] = ent.id
                stats["nodes"] += 1
                return ent.id

            # Helper to add relationship
            # Maintain local dictionary for edges to avoid duplicate queries and conflicts
            pending_edges = {}

            # Helper to add relationship
            def add_edge(src_code: str, dst_code: str, rtype: str, weight: float = 1.0, props: dict = None):
                src_norm = str(src_code).strip().upper()
                dst_norm = str(dst_code).strip().upper()

                src_id = code_id_map.get(src_norm)
                dst_id = code_id_map.get(dst_norm)

                if not src_id or not dst_id:
                    return

                key = (src_id, dst_id, rtype)
                if key in pending_edges:
                    old_weight, old_props = pending_edges[key]
                    if weight > old_weight:
                        pending_edges[key] = (float(weight), props or old_props)
                else:
                    pending_edges[key] = (float(weight), props or {})

            # ----------------------------------------------------
            # 2. INGEST NODES FROM DOMAIN TABLES
            # ----------------------------------------------------

            # Shop
            shops = session.query(Shop).all()
            for s in shops:
                add_entity("shop", f"SHOP_{s.shop_code}", s.name, {"code": s.shop_code})

            # Stations
            stations = session.query(Station).all()
            for st in stations:
                add_entity("station", f"STATION_{st.station_code}", st.name, {
                    "code": st.station_code,
                    "shop": st.shop.name if st.shop else "N/A"
                })

            # Processes
            processes = session.query(Process).all()
            for p in processes:
                add_entity("process", f"PROCESS_{p.process_code}", p.name, {"code": p.process_code})

            # Operations
            operations = session.query(Operation).all()
            for op in operations:
                add_entity("operation", f"OP_{op.operation_code}", op.name, {
                    "code": op.operation_code,
                    "summary": op.operation_summary or "",
                    "skill_part": op.skill_part or ""
                })

            # Skills
            skills = session.query(Skill).all()
            for sk in skills:
                add_entity("skill", f"SKILL_{sk.skill_code}", sk.name, {
                    "code": sk.skill_code,
                    "skill_part": sk.skill_part or ""
                })

            # Tools
            tools = session.query(Tool).all()
            for t in tools:
                add_entity("tool", f"TOOL_{t.tool_code}", t.name, {
                    "code": t.tool_code,
                    "description": t.description or ""
                })

            # Diploma
            diplomas = session.query(Diploma).all()
            for d in diplomas:
                add_entity("diploma", f"DIP_{d.code}", d.name, {"code": d.code})

            # Semester
            semesters = session.query(Semester).all()
            for sem in semesters:
                add_entity("semester", f"SEM_{sem.diploma.code}_S{sem.number}", f"Semester {sem.number}", {
                    "diploma": sem.diploma.name,
                    "number": sem.number
                })

            # Subject (Topic column is treated as subject)
            topics = session.query(Topic).all()
            for tp in topics:
                add_entity("subject", f"SUBJ_{tp.topic_code}", tp.title, {"code": tp.topic_code})

            # Topic / Subtopic (Subtopic is treated as granular theory topic)
            subtopics = session.query(Subtopic).all()
            for stp in subtopics:
                add_entity("topic", f"TOPIC_{stp.subtopic_code}", stp.title, {
                    "code": stp.subtopic_code,
                    "matched_operation": stp.matched_operation or "",
                    "skill_part": stp.skill_part or ""
                })

            session.flush()

            # ----------------------------------------------------
            # 3. BUILD EDGES / RELATIONSHIPS (Bidirectional & Directed)
            # ----------------------------------------------------

            # Shop -> Station
            for st in stations:
                if st.shop:
                    add_edge(f"SHOP_{st.shop.shop_code}", f"STATION_{st.station_code}", "has_station")
                    add_edge(f"STATION_{st.station_code}", f"SHOP_{st.shop.shop_code}", "performed_at")

            # Station -> Process
            for p in processes:
                if p.station:
                    add_edge(f"STATION_{p.station.station_code}", f"PROCESS_{p.process_code}", "has_process")
                    add_edge(f"PROCESS_{p.process_code}", f"STATION_{p.station.station_code}", "performed_at")

            # Process -> Operation
            for op in operations:
                if op.process:
                    add_edge(f"PROCESS_{op.process.process_code}", f"OP_{op.operation_code}", "has_operation")
                    add_edge(f"OP_{op.operation_code}", f"PROCESS_{op.process.process_code}", "performed_at")

            # Station -> Tool (via ToolStationMap)
            tool_stn_maps = session.query(ToolStationMap).all()
            for link in tool_stn_maps:
                stn = link.station
                tl = link.tool
                if stn and tl:
                    add_edge(f"STATION_{stn.station_code}", f"TOOL_{tl.tool_code}", "uses")
                    add_edge(f"TOOL_{tl.tool_code}", f"STATION_{stn.station_code}", "related_to")

            # Skill -> Operation (via SkillOperationMap)
            skill_op_maps = session.query(SkillOperationMap).all()
            for link in skill_op_maps:
                sk = link.skill
                op = link.operation
                if sk and op:
                    add_edge(f"OP_{op.operation_code}", f"SKILL_{sk.skill_code}", "requires", link.confidence)
                    add_edge(f"SKILL_{sk.skill_code}", f"OP_{op.operation_code}", "mapped_to", link.confidence)

            # Topic (Subject) -> Subtopic
            for stp in subtopics:
                if stp.topic:
                    add_edge(f"SUBJ_{stp.topic.topic_code}", f"TOPIC_{stp.subtopic_code}", "has_topic")
                    add_edge(f"TOPIC_{stp.subtopic_code}", f"SUBJ_{stp.topic.topic_code}", "studied_in")

            # Semester -> Topic (Subject)
            for tp in topics:
                if tp.semester:
                    add_edge(f"SEM_{tp.semester.diploma.code}_S{tp.semester.number}", f"SUBJ_{tp.topic_code}", "has_subject")
                    add_edge(f"SUBJ_{tp.topic_code}", f"SEM_{tp.semester.diploma.code}_S{tp.semester.number}", "studied_in")

            # Diploma -> Semester
            for sem in semesters:
                if sem.diploma:
                    add_edge(f"DIP_{sem.diploma.code}", f"SEM_{sem.diploma.code}_S{sem.number}", "has_semester")
                    add_edge(f"SEM_{sem.diploma.code}_S{sem.number}", f"DIP_{sem.diploma.code}", "studied_in")

            # Topic (Subtopic) -> Skill (via TopicSkillMap)
            topic_sk_maps = session.query(TopicSkillMap).all()
            for link in topic_sk_maps:
                tp = link.topic  # this is a Topic model, maps to our Subject GraphEntity
                sk = link.skill
                if tp and sk:
                    add_edge(f"SUBJ_{tp.topic_code}", f"SKILL_{sk.skill_code}", "covers", link.confidence)
                    add_edge(f"SKILL_{sk.skill_code}", f"SUBJ_{tp.topic_code}", "related_to", link.confidence)

            # Let's map granular subtopics (which act as "topics") directly to skills if exact skill_part matches
            for stp in subtopics:
                if stp.skill_part:
                    for sk in skills:
                        if sk.skill_part and sk.skill_part.lower() == stp.skill_part.lower():
                            add_edge(f"TOPIC_{stp.subtopic_code}", f"SKILL_{sk.skill_code}", "covers", 1.0)
                            add_edge(f"SKILL_{sk.skill_code}", f"TOPIC_{stp.subtopic_code}", "related_to", 1.0)

            # ----------------------------------------------------
            # 4. INFERRED RELATIONSHIPS / ADVANCED MAPPINGS
            # ----------------------------------------------------

            # A. Workstation dependencies (Similar stations sharing tools or processes)
            for i in range(len(stations)):
                st1 = stations[i]
                tools1 = {ts.tool_id for ts in st1.tool_links}
                proc1 = {p.id for p in st1.processes}

                for j in range(i + 1, len(stations)):
                    st2 = stations[j]
                    tools2 = {ts.tool_id for ts in st2.tool_links}
                    proc2 = {p.id for p in st2.processes}

                    # Check tool overlap Jaccard
                    tool_intersect = tools1.intersection(tools2)
                    tool_union = tools1.union(tools2)
                    jaccard_tools = len(tool_intersect) / len(tool_union) if tool_union else 0.0

                    # Check process overlap
                    proc_intersect = proc1.intersection(proc2)
                    proc_union = proc1.union(proc2)
                    jaccard_proc = len(proc_intersect) / len(proc_union) if proc_union else 0.0

                    wt = 0.5 * jaccard_tools + 0.5 * jaccard_proc
                    if wt >= 0.2:  # strong overlap
                        add_edge(f"STATION_{st1.station_code}", f"STATION_{st2.station_code}", "depends_on", wt)
                        add_edge(f"STATION_{st2.station_code}", f"STATION_{st1.station_code}", "depends_on", wt)

            # B. Tool ↔ Related Process
            # If a tool is used at a station that has a process, link them
            for st in stations:
                for tlink in st.tool_links:
                    tl = tlink.tool
                    for p in st.processes:
                        if tl and p:
                            add_edge(f"TOOL_{tl.tool_code}", f"PROCESS_{p.process_code}", "related_to", 0.8)
                            add_edge(f"PROCESS_{p.process_code}", f"TOOL_{tl.tool_code}", "uses", 0.8)

            # C. Process ↔ Required Semester Knowledge
            # If process -> operation -> skill -> subject -> semester, link process to semester
            for p in processes:
                for op in p.operations:
                    for sk_link in op.skill_links:
                        sk = sk_link.skill
                        if sk:
                            for tsk in sk.topic_links:
                                tp = tsk.topic
                                if tp and tp.semester:
                                    add_edge(
                                        f"PROCESS_{p.process_code}",
                                        f"SEM_{tp.semester.diploma.code}_S{tp.semester.number}",
                                        "requires",
                                        0.7
                                    )

            # D. Skill ↔ Subject (Relevant Subjects)
            # Link via subtopic / topic skill mapping explicitly
            for sk in skills:
                for tsk in sk.topic_links:
                    tp = tsk.topic
                    if tp:
                        add_edge(f"SKILL_{sk.skill_code}", f"SUBJ_{tp.topic_code}", "related_to", tsk.confidence)
                        add_edge(f"SUBJ_{tp.topic_code}", f"SKILL_{sk.skill_code}", "covers", tsk.confidence)

            # Ingest all pending relationships in batch
            for (src_id, dst_id, rtype), (weight, props) in pending_edges.items():
                edge = GraphRelationship(
                    source_id=src_id,
                    target_id=dst_id,
                    rel_type=rtype,
                    weight=weight,
                    properties=props
                )
                session.add(edge)
                stats["edges"] += 1

            # Commit the built Graph
            session.commit()
            logger.info("Knowledge Graph successfully generated and stored.")

        except Exception as e:
            session.rollback()
            logger.error(f"Error rebuilding knowledge graph: {str(e)}", exc_info=True)
            raise

    return stats


class GraphRelationshipEngine:
    """
    Traverses and extracts sub-graph segments using DFS/BFS,
    computes expandable card details, and enables search expansions.
    """

    @staticmethod
    def traverse_relationships(start_code: str, max_depth: int = 3, min_weight: float = 0.1) -> dict:
        """
        Runs BFS starting from start_code up to max_depth.
        Returns unique nodes and edges compatible with Vis.js.
        """
        start_code = start_code.strip().upper()
        nodes = []
        edges = []
        visited_nodes = set()
        visited_edges = set()

        queue = deque([(start_code, 0)])

        with SessionLocal() as session:
            while queue:
                curr_code, depth = queue.popleft()

                # Get Current Entity
                ent = session.query(GraphEntity).filter_by(code=curr_code).first()
                if not ent:
                    continue

                if curr_code not in visited_nodes:
                    visited_nodes.add(curr_code)
                    nodes.append({
                        "id": ent.id,
                        "label": ent.name[:30] + ("..." if len(ent.name) > 30 else ""),
                        "title": f"[{ent.entity_type.upper()}] {ent.name}",
                        "group": ent.entity_type,
                        "code": ent.code
                    })

                if depth >= max_depth:
                    continue

                # Query all outward and inward edges
                rel_list = session.query(GraphRelationship).filter(
                    (GraphRelationship.source_id == ent.id) | (GraphRelationship.target_id == ent.id)
                ).filter(GraphRelationship.weight >= min_weight).all()

                for r in rel_list:
                    src = r.source
                    tgt = r.target

                    if not src or not tgt:
                        continue

                    edge_key = (src.code, tgt.code, r.rel_type)
                    if edge_key not in visited_edges:
                        visited_edges.add(edge_key)
                        edges.append({
                            "from": src.id,
                            "to": tgt.id,
                            "label": f"{r.rel_type} ({round(r.weight, 2)})",
                            "weight": r.weight
                        })

                        # Add neighbors to queue
                        neighbor_code = tgt.code if src.code == curr_code else src.code
                        if neighbor_code not in visited_nodes:
                            queue.append((neighbor_code, depth + 1))

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def resolve_industrial_context(session, ent) -> dict:
        """
        Traverses the unified graph (depth <= 4) to find the primary associated 
        Station, Process, Shop, and Tools for any GraphEntity.
        """
        context = {
            "shop": "Trim Line Shop",
            "station": None,
            "process": None,
            "tools": []
        }
        
        def get_neighbors_by_type(node_id, types):
            rels = session.query(GraphRelationship).filter(
                (GraphRelationship.source_id == node_id) | (GraphRelationship.target_id == node_id)
            ).all()
            results = []
            for r in rels:
                neighbor = r.target if r.source_id == node_id else r.source
                if neighbor and neighbor.entity_type.lower() in types:
                    results.append(neighbor)
            return results

        # Simple BFS up to depth 4 to locate nearest station & process
        queue = [(ent, 0)]
        visited = {ent.id}
        nearest_station = None
        nearest_process = None
        
        while queue:
            curr, depth = queue.pop(0)
            
            if curr.entity_type.lower() == "station" and not nearest_station:
                nearest_station = curr
            if curr.entity_type.lower() == "process" and not nearest_process:
                nearest_process = curr
                
            if nearest_station and nearest_process:
                break
                
            if depth < 4:
                rels = session.query(GraphRelationship).filter(
                    (GraphRelationship.source_id == curr.id) | (GraphRelationship.target_id == curr.id)
                ).all()
                for r in rels:
                    neighbor = r.target if r.source_id == curr.id else r.source
                    if neighbor and neighbor.id not in visited:
                        visited.add(neighbor.id)
                        queue.append((neighbor, depth + 1))
                        
        if nearest_station:
            context["station"] = {
                "id": nearest_station.id,
                "name": nearest_station.name,
                "code": nearest_station.code
            }
            if nearest_station.properties and isinstance(nearest_station.properties, dict):
                shop_val = nearest_station.properties.get("shop") or nearest_station.properties.get("shop_code")
                if shop_val:
                    context["shop"] = shop_val
                    
            station_tools = get_neighbors_by_type(nearest_station.id, ["tool"])
            for tool in station_tools:
                context["tools"].append({
                    "id": tool.id,
                    "name": tool.name,
                    "code": tool.code
                })
                
        if nearest_process:
            context["process"] = {
                "id": nearest_process.id,
                "name": nearest_process.name,
                "code": nearest_process.code
            }
        else:
            if nearest_station:
                st_processes = get_neighbors_by_type(nearest_station.id, ["process"])
                if st_processes:
                    context["process"] = {
                        "id": st_processes[0].id,
                        "name": st_processes[0].name,
                        "code": st_processes[0].code
                    }
                    
        if not context["tools"]:
            direct_tools = get_neighbors_by_type(ent.id, ["tool"])
            for tool in direct_tools:
                context["tools"].append({
                    "id": tool.id,
                    "name": tool.name,
                    "code": tool.code
                })

        seen_tool_ids = set()
        unique_tools = []
        for t in context["tools"]:
            if t["id"] not in seen_tool_ids:
                seen_tool_ids.add(t["id"])
                unique_tools.append(t)
        context["tools"] = unique_tools
        
        if not context["shop"]:
            context["shop"] = "Trim Line Shop"
            
        return context

    @staticmethod
    def get_expanded_card_details(entity_type: str, entity_id: int) -> dict:
        """
        Returns full detailed profile of any node and all direct or 2-hop connected details,
        such as related skills, suggested training, similar stations, etc.
        """
        with SessionLocal() as session:
            # Translate original domain entity_id + entity_type to correct GraphEntity code
            code = None
            etype_lower = entity_type.lower()
            
            if etype_lower == "station":
                st = session.get(Station, entity_id)
                if st: code = f"STATION_{st.station_code}"
            elif etype_lower == "process":
                pr = session.get(Process, entity_id)
                if pr: code = f"PROCESS_{pr.process_code}"
            elif etype_lower == "operation":
                op = session.get(Operation, entity_id)
                if op: code = f"OP_{op.operation_code}"
            elif etype_lower == "skill":
                sk = session.get(Skill, entity_id)
                if sk: code = f"SKILL_{sk.skill_code}"
            elif etype_lower == "tool":
                tl = session.get(Tool, entity_id)
                if tl: code = f"TOOL_{tl.tool_code}"
            elif etype_lower == "topic":  # subject
                tp = session.get(Topic, entity_id)
                if tp: code = f"SUBJ_{tp.topic_code}"
            elif etype_lower == "subtopic":  # topic
                stp = session.get(Subtopic, entity_id)
                if stp: code = f"TOPIC_{stp.subtopic_code}"
            elif etype_lower == "shop":
                sh = session.get(Shop, entity_id)
                if sh: code = f"SHOP_{sh.shop_code}"

            ent = None
            if code:
                ent = session.query(GraphEntity).filter_by(code=code.upper()).first()
                
            # Fallback to direct GraphEntity lookup by ID if translation yielded nothing
            if not ent:
                ent = session.query(GraphEntity).filter_by(id=entity_id).first()

            if not ent:
                return {"error": f"Entity not found: {entity_type} #{entity_id}"}

            profile = {
                "id": ent.id,
                "type": ent.entity_type,
                "code": ent.code,
                "name": ent.name,
                "properties": ent.properties or {},
                "stations": [],
                "processes": [],
                "operations": [],
                "skills": [],
                "tools": [],
                "topics": [],
                "subjects": [],
                "semesters": [],
                "diplomas": [],
                "dependencies": [],
                "learning_modules": [],
                "relevance_score": 1.0,
                "industrial_context": GraphRelationshipEngine.resolve_industrial_context(session, ent)
            }

            # 2. Get directly connected neighbors
            direct_rels = session.query(GraphRelationship).filter(
                (GraphRelationship.source_id == ent.id) | (GraphRelationship.target_id == ent.id)
            ).all()

            connected_ids = set()
            for r in direct_rels:
                neighbor = r.target if r.source_id == ent.id else r.source
                if neighbor and neighbor.id != ent.id:
                    connected_ids.add((neighbor, r.rel_type, r.weight))

            # Populate directly connected nodes
            for n, rel, wt in connected_ids:
                item = {"id": n.id, "code": n.code, "name": n.name, "relation": rel, "weight": round(wt, 2)}
                t = n.entity_type
                if t == "station":
                    profile["stations"].append(item)
                elif t == "process":
                    profile["processes"].append(item)
                elif t == "operation":
                    profile["operations"].append(item)
                elif t == "skill":
                    profile["skills"].append(item)
                elif t == "tool":
                    profile["tools"].append(item)
                elif t == "topic":
                    profile["topics"].append(item)
                elif t == "subject":
                    profile["subjects"].append(item)
                elif t == "semester":
                    profile["semesters"].append(item)
                elif t == "diploma":
                    profile["diplomas"].append(item)

                if rel == "depends_on" or rel == "depends":
                    profile["dependencies"].append(item)

            # 3. Handle 2-hop connected theory as learning modules
            # (e.g. if looking at a Station, direct processes -> direct ops -> skills -> topics)
            # Let's run a small traversal to gather learning suggestions
            subgraph = GraphRelationshipEngine.traverse_relationships(ent.code, max_depth=2)
            seen_topics = set()
            for node in subgraph["nodes"]:
                if node["group"] in ["topic", "subject"] and node["code"] != ent.code:
                    if node["code"] not in seen_topics:
                        seen_topics.add(node["code"])
                        profile["learning_modules"].append({
                            "id": node["id"],
                            "code": node["code"],
                            "name": node["title"],
                            "type": node["group"]
                        })

            return profile

    @staticmethod
    def search_and_expand_ecosystem(query_string: str, limit: int = 20) -> dict:
        """
        Runs FTS5 search to find matching entities, then expands their graph relationships
        recursively (depth=2) to return a unified ecosystem of direct and connected entities.
        """
        from search_engine import SearchAPI

        # 1. Run FTS5 search
        direct_matches = SearchAPI.search(query_string, limit=limit)

        nodes = []
        edges = []
        visited_nodes = set()
        visited_edges = set()
        direct_codes = set()

        with SessionLocal() as session:
            for m in direct_matches:
                etype_lower = m["entity_type"].lower()
                entity_id = m["entity_id"]
                
                code = None
                if etype_lower == "station":
                    st = session.get(Station, entity_id)
                    if st: code = f"STATION_{st.station_code}"
                elif etype_lower == "process":
                    pr = session.get(Process, entity_id)
                    if pr: code = f"PROCESS_{pr.process_code}"
                elif etype_lower == "operation":
                    op = session.get(Operation, entity_id)
                    if op: code = f"OP_{op.operation_code}"
                elif etype_lower == "skill":
                    sk = session.get(Skill, entity_id)
                    if sk: code = f"SKILL_{sk.skill_code}"
                elif etype_lower == "tool":
                    tl = session.get(Tool, entity_id)
                    if tl: code = f"TOOL_{tl.tool_code}"
                elif etype_lower == "topic":
                    tp = session.get(Topic, entity_id)
                    if tp: code = f"SUBJ_{tp.topic_code}"
                elif etype_lower == "subtopic":
                    stp = session.get(Subtopic, entity_id)
                    if stp: code = f"TOPIC_{stp.subtopic_code}"
                elif etype_lower == "shop":
                    sh = session.get(Shop, entity_id)
                    if sh: code = f"SHOP_{sh.shop_code}"

                if code:
                    direct_codes.add(code.upper())

            # Now run BFS / traversal for each direct match to expand relationships
            for code in direct_codes:
                subgraph = GraphRelationshipEngine.traverse_relationships(code, max_depth=2)

                # Merge nodes
                for node in subgraph["nodes"]:
                    if node["code"] not in visited_nodes:
                        visited_nodes.add(node["code"])
                        node_data = dict(node)
                        node_data["is_direct"] = node["code"] in direct_codes
                        nodes.append(node_data)

                # Merge edges
                for edge in subgraph["edges"]:
                    edge_key = (edge["from"], edge["to"], edge.get("label"))
                    if edge_key not in visited_edges:
                        visited_edges.add(edge_key)
                        edges.append(edge)

        return {
            "direct_matches": direct_matches,
            "graph": {"nodes": nodes, "edges": edges}
        }

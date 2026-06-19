from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from models import (
    Topic, Subtopic, Tool, Station, Skill, 
    SkillStationMap, ToolStationMap, StationOperationMap
)
from database import SessionLocal

@dataclass
class UnifiedSearchEntity:
    id: str
    name: str
    entityType: str
    metadata: Dict[str, Any]
    learningFootprint: List[Dict[str, Any]] = field(default_factory=list)
    syllabusBreakdown: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return asdict(self)

def _gather_station_tools(session, station: Station) -> List[str]:
    return [link.tool.name for link in station.tool_links if link.tool and link.tool.name]

def _gather_skills_for_station(session, station: Station) -> List[str]:
    return [link.skill.name for link in station.skill_links if link.skill and link.skill.name]

def _gather_topics_for_station(session, station: Station) -> List[str]:
    topics = []
    for sk_link in station.skill_links:
        if sk_link.skill:
            for tp_link in sk_link.skill.topic_links:
                if tp_link.topic and tp_link.topic.title not in topics:
                    topics.append(tp_link.topic.title)
    return topics

def _build_footprint_for_topic(session, topic: Topic) -> List[Dict[str, Any]]:
    footprints = []
    skill_ids = [link.skill_id for link in topic.skill_links if link.skill]
    if not skill_ids:
        return footprints

    links = session.query(SkillStationMap).filter(SkillStationMap.skill_id.in_(skill_ids)).all()
    for link in links:
        station = session.get(Station, link.station_id)
        if not station: 
            continue
        ops = [som.operation.name for som in station.operation_links if som.operation]
        footprints.append({
            "shopName": station.shop.name if station.shop else "Production Floor",
            "stationNo": station.raw_station_id or station.station_code,
            "associatedTools": _gather_station_tools(session, station),
            "practicalOperation": ops[0] if ops else "Hands-on syllabus application execution"
        })
    return footprints

def _build_footprint_for_tool(session, tool: Tool) -> List[Dict[str, Any]]:
    footprints = []
    for link in tool.station_links:
        station = session.get(Station, link.station_id)
        if not station: 
            continue
        ops = [som.operation.name for som in station.operation_links if som.operation]
        footprints.append({
            "shopName": station.shop.name if station.shop else "Production Floor",
            "stationNo": station.raw_station_id or station.station_code,
            "associatedTools": [tool.name],
            "practicalOperation": ops[0] if ops else "Equipment tooling operational processing"
        })
    return footprints

def build_unified_search_entity(entity_type: str, entity_id: int) -> Optional[Dict[str, Any]]:
    entity_type = entity_type.lower()
    with SessionLocal() as session:
        
        # ── PARAMETER MATRIX 1: SYLLABUS TOPIC OR SUBTOPIC
        if entity_type in ["topic", "subtopic"]:
            if entity_type == "subtopic":
                sub = session.get(Subtopic, entity_id)
                if not sub or not sub.topic: 
                    return None
                topic = sub.topic
            else:
                topic = session.get(Topic, entity_id)
                if not topic: 
                    return None

            semester = topic.semester
            diploma = semester.diploma if semester else None
            
            return UnifiedSearchEntity(
                id=f"topic-{topic.id}",
                name=topic.title,
                entityType="THEORY_TOPIC",
                metadata={
                    "description": f"Theoretical learning concept mapped directly to real-world plant floor production operations.",
                    "academicContext": {
                        "courseName": diploma.name if diploma else "Engineering Program",
                        "semester": f"Semester {semester.number}" if semester else "N/A",
                        "unitClassification": f"Topic Code: {topic.topic_code}"
                    }
                },
                learningFootprint=_build_footprint_for_topic(session, topic),
                syllabusBreakdown={
                    "parentTopic": topic.title,
                    "subTopics": [st.title for st in topic.subtopics] if topic.subtopics else []
                }
            ).to_dict()

        # ── PARAMETER MATRIX 2: INDUSTRIAL TOOL
        if entity_type == "tool":
            tool = session.get(Tool, entity_id)
            if not tool: 
                return None
            
            mapped_topics = []
            for st_link in tool.station_links:
                if st_link.station:
                    mapped_topics.extend(_gather_topics_for_station(session, st_link.station))
            mapped_topics = list(set(mapped_topics))

            return UnifiedSearchEntity(
                id=f"tool-{tool.id}",
                name=tool.name,
                entityType="TOOL",
                metadata={
                    "description": tool.description or "Active floor equipment asset allocated to plant workstations.",
                    "academicContext": {
                        "courseName": "Workshop Tooling Application",
                        "semester": "Cross-Shop Infrastructure",
                        "unitClassification": "Manufacturing Asset"
                    }
                },
                learningFootprint=_build_footprint_for_tool(session, tool),
                syllabusBreakdown={
                    "parentTopic": "Core Course Alignment",
                    "subTopics": mapped_topics if mapped_topics else ["Practical application field operations"]
                }
            ).to_dict()

        # ── PARAMETER MATRIX 3: STATION NO LOOKUP
        if entity_type == "station":
            station = session.get(Station, entity_id)
            if not station: 
                return None
            
            mapped_topics = _gather_topics_for_station(session, station)
            ops = [som.operation.name for som in station.operation_links if som.operation]

            return UnifiedSearchEntity(
                id=f"station-{station.id}",
                name=station.raw_station_id or station.name,
                entityType="STATION",
                metadata={
                    "description": f"Active factory floor production workstation positioned inside the {station.shop.name if station.shop else 'Plant Floor'}.",
                    "academicContext": {
                        "courseName": "Plant Floor Layout Mapping",
                        "semester": station.shop.shop_code if station.shop else "N/A",
                        "unitClassification": f"Station ID Code: {station.station_code}"
                    }
                },
                learningFootprint=[{
                    "shopName": station.shop.name if station.shop else "Plant Floor",
                    "stationNo": station.raw_station_id or station.station_code,
                    "associatedTools": _gather_station_tools(session, station),
                    "practicalOperation": ops[0] if ops else "General assembly execution workflow"
                }],
                syllabusBreakdown={
                    "parentTopic": "Demonstrated Educational Concepts",
                    "subTopics": mapped_topics if mapped_topics else ["Practical operational performance items"]
                }
            ).to_dict()

        # ── PARAMETER MATRIX 4: OPERATIONAL SKILL
        if entity_type == "skill":
            skill = session.get(Skill, entity_id)
            if not skill: 
                return None

            footprints = []
            links = session.query(SkillStationMap).filter(SkillStationMap.skill_id == skill.id).all()
            for link in links:
                stn = session.get(Station, link.station_id)
                if stn:
                    ops = [som.operation.name for som in stn.operation_links if som.operation]
                    footprints.append({
                        "shopName": stn.shop.name if stn.shop else "Plant Floor",
                        "stationNo": stn.raw_station_id or stn.station_code,
                        "associatedTools": _gather_station_tools(session, stn),
                        "practicalOperation": ops[0] if ops else "Workstation competency processing line"
                    })

            mapped_topics = [link.topic.title for link in skill.topic_links if link.topic]

            return UnifiedSearchEntity(
                id=f"skill-{skill.id}",
                name=skill.name,
                entityType="SKILL",
                metadata={
                    "description": f"Standard operating competency requirements: {skill.skill_part or 'General Processing Work'}.",
                    "academicContext": {
                        "courseName": "Vocational Capability Framework",
                        "semester": "Skill Mapping Coordinates",
                        "unitClassification": skill.skill_code
                    }
                },
                learningFootprint=footprints,
                syllabusBreakdown={
                    "parentTopic": "Underlying Theoretical Curriculum Modules",
                    "subTopics": mapped_topics if mapped_topics else ["Industrial application tracking fields"]
                }
            ).to_dict()

        return None
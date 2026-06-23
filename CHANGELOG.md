# CHANGE LOG

## Version 2.1

### Completed

- Shop hierarchy dashboard
- Station hierarchy dashboard
- WIS Repository
- Shop-level WIS uploads
- Station-level WIS uploads
- PPT/PPTX viewing
- WIS viewer page
- Breadcrumb fixes
- Independent BIW shop architecture

### Manufacturing Shops
TCF_1

TCF_2

PAINT_SHOP

ENGINE_SHOP

TRANSAXLE_SHOP

EV_SHOP

JLR_SHOP

X1_BIW

Q5_BIW

X4_BIW

NOVA_BIW

SYLLABUS

### Deprecated
WELD_SHOP

Reason:

Replaced by:

X1_BIW
Q5_BIW
X4_BIW
NOVA_BIW

Legacy code retained temporarily for backward compatibility.

### Upload Architecture
Excel Upload
 Shop Selected
 Shop Schema Selected
 Hierarchy Generated
 Stations Created/Updated

WIS Upload
 Shop Page
 Shop WIS Repository

WIS Upload
 Station Page
 Station WIS Repository

No AI Extraction.

No PPT Parsing.

Storage and Viewing Only.

### Shop Schema Definitions
X1_BIW

LINE
ZONE NO
ZONE DESCRIPTION
STATION NO
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

PAINT

LINE
ZONE NO
ZONE DESCRIPTION
STATION NO
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

EV_SHOP

CELL
LINE
ZONE NO
ZONE DESCRIPTION
STATION NO
STATION DESCRIPTION
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

Q5_BIW

CELL
LINE
ZONE NO
ZONE DESCRIPTION
STATION NO
STATION DESCRIPTION
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

X4_BIW

CELL
LINE
ZONE
STATION NO
STATION DESCRIPTION
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

NOVA_BIW

LINE
STATION NO
ZONE
STATION DESCRIPTION
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

ENGINE_SHOP

SHOP
CELL
LINE
ZONE NO
STATIONS
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

TCF_1

SHOP
CELL
LINE
ZONE NO
STATIONS
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

TCF_2

SHOP
STATIONS
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

TRANSAXLE_SHOP

SHOP
CELL
LINE
ZONE NO
STATIONS
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

JLR_SHOP

SHOP
CELL
LINE
ZONE NO
STATIONS
PROCESS
TOOLS / EQUIPMENT
OPERATION SUMMARY
SKILL PART

### Future Architecture
Incremental Upload Support

Upload 1:
Hierarchy

Upload 2:
Process

Upload 3:
Tools / Equipment

Upload 4:
Operation Summary

Upload 5:
Skill Part

Missing values must enrich existing stations.

No station recreation.

No hierarchy loss.

UPSERT architecture required.

### Important Constraints
Never modify competency engine unless explicitly requested.

Never modify graph engine unless explicitly requested.

Never modify WIS repository unless explicitly requested.

Never remove backward compatibility without verification.

Always read PROJECT_CONTEXT.md before making changes.

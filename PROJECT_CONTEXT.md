# Project Context: Industrial Competency Intelligence System

## Overview

This repository is a Python + Flask application for ingesting shop Excel exports, building an industrial competency knowledge graph, and associating shop knowledge assets with shops and stations.

There are two copies of the project in the workspace: the root-level version and a nested duplicate under Industrial-Competency-Intelligence-System/. The root-level files are the primary working copy in this session.

## Core Architecture

### Key entry points
- app.py — Flask web application, admin upload UI, shop/station pages, workbook upload and preview routes.
- data_engine.py — ETL pipeline for Excel ingestion, schema normalization, and shop alias handling.
- models.py — SQLAlchemy ORM data model including workbook-level and sheet-level document mappings.
- templates/ — Jinja2 UI templates, including shop.html and station.html.
- uploads/ — persisted upload storage for workbook assets and inspection artifacts.

### Execution flow
1. Admin uploads an Excel workbook or shop-level workbook package in the admin UI.
2. app.py routes workbook uploads to the shop-level inspection workflow.
3. data_engine.py reads any Excel input with pandas and applies a shop-specific schema when available.
4. Dataset type is detected using normalized headers.
5. The appropriate ETL class ingests rows into the database and stores raw row copies in StagingData.
6. WIS and PPE workbook assets are stored at the shop level and mapped to stations through sheet mapping records.

## Workbook-based architecture (Version 2.3)

### Design goals
- Use shop-level WIS and PPE workbooks as the primary document structure.
- Keep workbook → sheet → station mapping explicit and persistent.
- Do not introduce station-level uploads.
- Do not perform AI extraction.
- Do not perform competency mapping as part of workbook inspection.
- Limit the workflow to inspection and viewing of uploaded workbook content.

### Model layer
- ShopWISWorkbook — shop-level WIS workbook record with version_number, active, archived_at, and change_summary.
- StationWISSheet — sheet-level mapping from a workbook sheet to a station.
- ShopPPEWorkbook — shop-level PPE workbook record with version_number, active, archived_at, and change_summary.
- StationPPESheet — sheet-level mapping from a PPE workbook sheet to a station.

### UI behavior
- The shop page exposes WIS and PPE workbook upload and inspection sections.
- The station page exposes a PPE inspection tab with sheet previews and mapping details.
- The admin UI includes a Press Shop selection tile.

### Restrictions
- No station-level document upload routes are active.
- No AI-driven extraction is invoked for workbook content.
- No competency graph updates are triggered from workbook inspection.
- A shop may have only one active WIS workbook and one active PPE workbook at a time.

## Shop-specific Excel ingestion

### Shop schema mapping
- data_engine.py defines SHOP_SCHEMAS for normalized shop keys such as X1 BIW, PAINT SHOP, EV SHOP, Q5 BIW, NOVA BIW, TCF 1, and others.
- Each schema lists the expected display column names.
- When a shop override is provided, ingest_excel() applies _normalize_columns_with_schema() before type detection.
- Shop-specific normalization is tolerant of case, punctuation, dots, underscores, and alias variations.

### SHOP_ALIASES mapping
- A non-destructive SHOP_ALIASES mapping bridges UI/shop codes such as X1_BIW to display keys used by SHOP_SCHEMAS such as X1 BIW.
- This prevents schema lookup failures without renaming SHOP_SCHEMAS keys or changing UI codes.
- Entries include X1_BIW -> X1 BIW, Q5_BIW -> Q5 BIW, X4_BIW -> X4 BIW, NOVA_BIW -> NOVA BIW, PAINT_SHOP -> PAINT SHOP, EV_SHOP -> EV SHOP, ENGINE_SHOP -> ENGINE SHOP, TRANSAXLE_SHOP -> TRANSAXLE SHOP, PRESS_SHOP -> PRESS SHOP, TCF_1 -> TCF 1, TCF_2 -> TCF 2, and JLR_SHOP -> TJLR.

## Version 2.3

- Implemented the workbook-based WIS and PPE architecture with shop-level workbook records and sheet-to-station mapping.
- Added workbook versioning so each shop keeps one active WIS workbook and one active PPE workbook, archives older versions, and preserves history.
- Added workbook metadata for version number, active state, archive timestamp, and change summary.
- Updated sheet-to-station mapping persistence to avoid duplicate mapping rows per workbook version and to merge forward values for partial enrichment uploads.
- Removed the old station-level WIS upload flow in favor of shop-level workbook inspection.
- Added Press Shop support across the UI, admin selection, and ingestion alias handling.
- Fixed the Transaxle display issue by making shop resolution more tolerant of alternate shop codes and alias formats.
- Kept workbook inspection read-only and did not add AI extraction or competency mapping behavior.

## ETL and data model behavior

### Station data ingestion
- StationDataETL.ingest() normalizes rows and cleans values.
- It stages every raw row in StagingData before row-level savepoints.
- Duplicate rows inside the batch are skipped using _row_fingerprint().
- Row-level failures roll back only the row and mark the staging record FAILED.
- The ingestion path remains tolerant of missing optional columns.

### Entities created
- Shop — created or updated using a normalized shop code and preserved display name.
- Station — created with station_code, raw_station_id, name, cell, line, zone_no, row_order, and shop_id.
- Process — created per station and normalized with the station code.
- Operation — created per process with operation_summary and skill_part.
- Skill — created for non-blank skill_part values and linked to operations and stations.
- Tool — created for each parsed tool/equipment entry and linked to stations.
- StationOperationMap connects stations to operations.

## WIS and PPE workflow

### Uploading
- app.py routes shop-level workbook uploads for WIS and PPE inspection.
- Workbooks are stored under uploads/ and linked to shop records through workbook tables.
- Sheet-to-station mapping is persisted in StationWISSheet and StationPPESheet.

### Retrieval and viewing
- The shop and station pages render workbook metadata and sheet previews.
- The workflow is inspection-focused and does not attempt extraction or mapping beyond the stored sheet relationships.

### Station WIS Viewing Architecture
- Shop uploads create a ShopWISWorkbook record and a set of StationWISSheet mappings.
- The station page exposes a dedicated WIS tab that lists the mapped workbook sheets for that station.
- Each WIS mapping shows the workbook name, sheet name, upload timestamp, uploaded-by value, match status, and an action to open the original workbook file.
- The open action resolves the mapped sheet back to its ShopWISWorkbook file and serves the original Excel workbook without parsing or transforming its contents.

## Important model pieces

### Upload registry
- UploadedFile records ingestion events, duplicates, and reprocess attempts.
- Fields include filename, file_hash, shop_code, uploaded_by, upload_mode, status, and upload_time.

### Knowledge graph relationships
- SkillOperationMap, ToolStationMap, SkillStationMap, and StationOperationMap remain the primary operational links.
- GraphEntity and GraphRelationship are available for higher-level traversal but are not the immediate target of workbook inspection.

## UI / rendering behavior

- templates/shop.html renders shop pages with hierarchy and workbook inspection sections.
- templates/station.html renders station detail views with PPE inspection content.
- Station ordering preserves Station.row_order to reflect original Excel row order.

## Practical rules for future agents

- Preserve the current SHOP_SCHEMAS and header normalization behavior.
- Keep missing-column tolerance, row-level savepoints, and row_order preservation.
- Keep workbook inspection read-only and separate from ingestion and competency logic.
- Use the root-level files as canonical source; the nested copy is a duplicate snapshot.

## Key files
- /app.py
- /data_engine.py
- /models.py
- /templates/shop.html
- /templates/station.html
- /uploads/

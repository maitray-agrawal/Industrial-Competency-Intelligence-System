# Project Context: Industrial Competency Intelligence System

## Overview

This repository is a Python + Flask application for ingesting shop Excel exports, building an industrial competency knowledge graph, and associating WIS (Work Instruction Set) documents with shops and stations.

There are two copies of the project in the workspace: the root-level version and a nested duplicate under `Industrial-Competency-Intelligence-System/`. The root-level files are the primary working copy in this session.

## Core Architecture

### Key entry points
- `app.py` — Flask web application, admin upload UI, shop/station pages, WIS upload/download/view routes.
- `data_engine.py` — ETL pipeline for Excel ingestion and dataset detection.
- `models.py` — SQLAlchemy ORM data model.
- `templates/` — Jinja2 UI templates, including `shop.html` and `wis-viewer.html`.
- `uploads/` — persisted upload storage including WIS files.

### Execution flow
1. Admin uploads an Excel file in the admin UI.
2. `app.py` calls `IngestionPipeline.ingest_excel()` from `data_engine.py`.
3. `data_engine.py` reads the Excel file with pandas and optionally applies a shop-specific schema.
4. Dataset type is detected: `station_data`, `station_details`, or `tcf_data`.
5. The appropriate ETL class ingests rows into the database and stores raw row copies in `StagingData`.
6. WIS documents are uploaded separately via dedicated admin routes and recorded in `ShopWISDocument` / `StationWISDocument`.

## Shop-specific Excel ingestion

### Shop schema mapping
- `data_engine.py` defines `SHOP_SCHEMAS`, a mapping from normalized shop keys like `X1 BIW`, `PAINT SHOP`, `EV SHOP`, `Q5 BIW`, `NOVA BIW`, `TCF 1`, etc.
- Each schema lists the shop's expected display column names.
- When a shop override is provided, `ingest_excel()` applies `_normalize_columns_with_schema()` before type detection.
- Shop-specific normalization is tolerant of case, punctuation, dots, underscores, and alias variations.

### Recent change — SHOP_ALIASES mapping
- A non-destructive `SHOP_ALIASES` mapping was added to `data_engine.py` to bridge UI/shop codes
  (underscore format, e.g. `X1_BIW`) to the display keys used by `SHOP_SCHEMAS` (space format, e.g. `X1 BIW`).
- This prevents schema lookup failures without renaming `SHOP_SCHEMAS` keys or changing UI codes.
- Mapping entries include: `X1_BIW -> X1 BIW`, `Q5_BIW -> Q5 BIW`, `X4_BIW -> X4 BIW`, `NOVA_BIW -> NOVA BIW`,
  `PAINT_SHOP -> PAINT SHOP`, `EV_SHOP -> EV SHOP`, `ENGINE_SHOP -> ENGINE SHOP`, `TRANSAXLE_SHOP -> TRANSAXLE SHOP`,
  `TCF_1 -> TCF 1`, `TCF_2 -> TCF 2`, `JLR_SHOP -> TJLR`.

Change rationale: keep `SHOP_SCHEMAS` stable (human-friendly display keys) while accepting canonical UI shop codes.

## Version 2.1.1

- Added `SHOP_ALIASES` layer to normalize UI/database shop codes to `SHOP_SCHEMAS` display names. This is a non-destructive mapping that ensures the admin UI shop codes (underscore form) resolve to existing shop schema keys (space form) without renaming schema keys or changing UI codes.

### Header normalization
- `_normalize_columns_with_schema()` maps raw Excel headers to normalized internal keys using a shop schema and canonical aliases.
- `_normalize_shop_columns()` handles generic shop data upload headers through slug matching and canonical mapping.
- Unknown/extra columns are dropped silently.
- The ingest pipeline continues when optional columns are missing.

### Dataset detection
- `detect_dataset_type()` uses slug matching on headers:
  - `tcf_data` if `topic` + `sub-topic` present.
  - `station_details` if `shop` + `station`/`stations` present.
  - `station_data` if station identifier plus another shop data field is present.
- If shop-specific schema is applied, ingestion is forced to `station_data`.

## ETL and data model behavior

### Station data ingestion
- `StationDataETL.ingest()` normalizes rows and cleans values.
- It stages every raw row in `StagingData` before row-level savepoints.
- Duplicate rows inside the batch are skipped using `_row_fingerprint()`.
- Row-level failures rollback only the row and mark the staging record `FAILED`.
- The ingestion is tolerant of missing optional columns and does not fail if columns are absent.

### Entities created
- `Shop` — created / updated using normalized shop code and preserved display name.
- `Station` — created with `station_code`, `raw_station_id`, `name`, `cell`, `line`, `zone_no`, `row_order`, and `shop_id`.
  - `row_order` is set to the Excel row index (`int(idx)`), preserving input order for hierarchy rendering.
- `Process` — created per station and normalized with the station code.
- `Operation` — created per process, with `operation_summary` and `skill_part`.
- `Skill` — created for non-blank `skill_part`, then linked to `Operation` and `Station`.
- `Tool` — created for each parsed tool/equipment entry and linked to `Station`.
- Shortcut link: `StationOperationMap` connects stations to operations.

### Upload modes
- `insert_only` — skip station rows whose stations already exist.
- `update_only` — skip rows where stations do not already exist.
- `upsert` — insert new rows and update existing rows.

### Missing column handling
- `StationDataETL` captures `missing_columns` in stats.
- Optional or unrecognized columns do not abort ingestion.
- Rows with blank required station identifiers are skipped with warnings.

## WIS document workflow

### Uploading
- `app.py` exposes admin routes:
  - `/admin/upload-shop-wis` — attach `.ppt` / `.pptx` to a shop.
  - `/admin/upload-station-wis` — attach `.ppt` / `.pptx` to a station.
- Files are saved under `uploads/wis/shops/<shop.name>/` or `uploads/wis/stations/<station.station_code>/`.
- Saved filenames include a UUID suffix to avoid collisions.
- Metadata is persisted in:
  - `ShopWISDocument` (`shop_id`, `file_name`, `file_path`, `uploaded_by`, `uploaded_at`)
  - `StationWISDocument` (`station_id`, `file_name`, `file_path`, `uploaded_by`, `uploaded_at`)

### Retrieval and viewing
- Download routes require auth for admin downloads:
  - `/download-shop-wis/<doc_id>`
  - `/download-station-wis/<doc_id>`
- Viewer routes serve documents for browser viewing:
  - `/view-shop-wis/<doc_id>`
  - `/view-station-wis/<doc_id>`
- Metadata viewer pages:
  - `/shop/wis/<doc_id>`
  - `/station/wis/<doc_id>`
- MIME types are resolved for PowerPoint and common media types.

## Important model pieces

### Upload registry
- `UploadedFile` records every ingestion event, including duplicates and reprocess attempts.
- Fields: `filename`, `file_hash`, `shop_code`, `uploaded_by`, `upload_mode`, `status`, `upload_time`.

### Knowledge graph relationships
- `SkillOperationMap`, `ToolStationMap`, `SkillStationMap`, `StationOperationMap` are used to link operational entities.
- `GraphEntity` / `GraphRelationship` are available for higher-level traversal but are not the immediate shop ingestion target.

## UI / rendering behavior

- `templates/shop.html` renders shop pages with an expandable hierarchy tree.
- Station ordering in the UI preserves `Station.row_order` to reflect original Excel row order.
- Shop-type-specific grouping and labels are supported in `app.py` when building the station/process hierarchy.

## Practical rules for future agents

- Do not replace shop-specific Excel ingestion with a single generic schema.
- Preserve the current `SHOP_SCHEMAS` mapping and header normalization behavior.
- Keep missing-column tolerance, row-level savepoints, and `row_order` preservation.
- WIS uploads should remain separate from ingestion and stored in `uploads/wis/`.
- Use the root-level files as canonical source; the nested copy is a duplicate snapshot.

## Key files
- `/app.py`
- `/data_engine.py`
- `/models.py`
- `/templates/shop.html`
- `/templates/wis-viewer.html`
- `/uploads/`

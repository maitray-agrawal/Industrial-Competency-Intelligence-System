# Workbook Architecture Verification Report

This report verifies the current workbook-based WIS/PPE implementation from the repository code only. No source files were modified.

## Summary

The workbook architecture is present and wired end-to-end for shop-level upload and inspection. The implementation stores workbook metadata, parses workbook sheet names with openpyxl, and creates sheet-to-station mapping rows. The main limitation is that updates are not merged or overwritten; each upload creates a new workbook record and new sheet mapping rows.

## Verification by item

### 1. How a shop head uploads WIS.xlsx
Status: IMPLEMENTED

Evidence:
- The shop-level upload form is rendered in [templates/shop.html](templates/shop.html#L174) and posts to the WIS workbook route.
- The route [app.py](app.py#L1096-L1104) exposes `upload_shop_wis_workbook` for POST uploads.
- The shared persistence helper [app.py](app.py#L995-L1093) validates the file, saves it, parses it, creates a `ShopWISWorkbook` record, and creates `StationWISSheet` rows for each worksheet.

### 2. How a shop head uploads PPE.xlsx
Status: IMPLEMENTED

Evidence:
- The PPE upload form is rendered in [templates/shop.html](templates/shop.html#L247) and posts to the PPE workbook route.
- The route [app.py](app.py#L1109-L1116) exposes `upload_shop_ppe_workbook` for POST uploads.
- The same helper [app.py](app.py#L995-L1093) handles PPE uploads, creates a `ShopPPEWorkbook` record, and creates `StationPPESheet` rows for each worksheet.

### 3. Whether openpyxl.load_workbook() is used
Status: IMPLEMENTED

Evidence:
- The import exists at [app.py](app.py#L8).
- The upload helper calls `load_workbook(filepath, read_only=True, data_only=True)` at [app.py](app.py#L1030-L1033).

### 4. Whether every worksheet becomes a station mapping unit
Status: IMPLEMENTED

Evidence:
- In the WIS branch of the helper, the code iterates over every worksheet name and creates one `StationWISSheet` row per sheet at [app.py](app.py#L1053-L1061).
- In the PPE branch, the code iterates over every worksheet name and creates one `StationPPESheet` row per sheet at [app.py](app.py#L1073-L1081).

### 5. How station names are matched to sheet names
Status: PARTIAL

Evidence:
- Matching is implemented by `_match_station_for_sheet` in [app.py](app.py#L69-L83).
- It normalizes the incoming sheet name and compares it against each station’s `station_code`, `raw_station_id`, and `name`.
- This is a heuristic, name-token-based match and can leave sheets unmatched when the sheet name does not closely resemble the station metadata.

### 6. How sheet updates overwrite or merge previous data
Status: NOT IMPLEMENTED

Evidence:
- The current helper always creates a new workbook record and a fresh set of sheet rows for each upload at [app.py](app.py#L1042-L1086).
- There is no update path, merge path, or delete/replace logic for existing workbook or sheet mappings.

### 7. Whether StationWISSheet rows are created
Status: IMPLEMENTED

Evidence:
- The helper adds `StationWISSheet` rows inside the `workbook_kind == "wis"` branch at [app.py](app.py#L1053-L1061).
- The ORM model is defined at [models.py](models.py#L504-L524).

### 8. Whether StationPPESheet rows are created
Status: IMPLEMENTED

Evidence:
- The helper adds `StationPPESheet` rows inside the PPE branch at [app.py](app.py#L1073-L1081).
- The ORM model is defined at [models.py](models.py#L547-L567).

### 9. Whether station.html reads workbook-derived data
Status: IMPLEMENTED

Evidence:
- The station route collects PPE sheet mappings and builds preview data at [app.py](app.py#L277-L303).
- It queries `StationPPESheet` records, reads workbook previews, and passes `ppe_sheet_views` into the template.
- The template renders workbook-derived PPE sheet data in [templates/station.html](templates/station.html#L225-L254).

### 10. Whether shop.html reads workbook-derived data
Status: IMPLEMENTED

Evidence:
- The shop route loads workbook records from the database and passes them to the template at [app.py](app.py#L513-L539).
- The template renders WIS and PPE workbook sections and the sheet-to-station mapping summary in [templates/shop.html](templates/shop.html#L174-L216) and [templates/shop.html](templates/shop.html#L247-L289).

## Conclusion

The workbook architecture is implemented for shop-level upload, parsing, sheet mapping, and inspection. The clear gap is that uploads are additive rather than update-aware; they do not overwrite or merge prior workbook/sheet state.

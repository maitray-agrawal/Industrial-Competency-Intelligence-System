# Workbook Versioning Verification Report

## Summary
Workbook versioning is now implemented for the shop-level WIS/PPE workflow without changing the UI. Each shop now supports one active WIS workbook and one active PPE workbook, while previous active versions are archived and preserved in history.

## What changed
- Added workbook version metadata to the ORM layer: version number, active state, archive timestamp, and change summary.
- Updated the workbook upload persistence flow to archive the previous active workbook before creating the new version.
- Ensured the new workbook version creates or updates sheet-to-station mappings without inserting duplicate rows for the same sheet name within the same workbook version.
- Added merge-forward behavior so partial enrichment uploads inherit values from the previous active workbook when the new upload leaves a field empty.

## Verification performed
1. Python syntax validation:
   - Ran: `python -m py_compile app.py models.py`
   - Result: passed with no output.
2. End-to-end workbook upload verification:
   - Uploaded two workbook versions for a temporary shop using the workbook persistence helper.
   - Verified that the first upload was archived, the second became active, and the version numbers progressed from 1 to 2.
   - Verified that the active workbook had exactly one mapping row for the uploaded sheet and that the archive entry remained preserved.

## Verified outcomes
- One active WIS workbook per shop is enforced by archiving the previous active workbook before creating the new version.
- Workbook history is preserved through archived rows rather than overwritten.
- Sheet-to-station mapping rows are update-safe and do not duplicate on repeated processing of the same sheet within a workbook version.
- The workflow remains inspection-focused and does not alter the existing UI behavior.

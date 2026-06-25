# Debug Report: Transaxle display issue

## Summary
The Transaxle shop page was not consistently resolving to the expected shop data because the application and ingestion layer used different shop-code formats. Some paths expected canonical UI codes such as TRANSAXLE_SHOP, while other paths relied on display names or schema aliases.

## Root cause
- Shop resolution was inconsistent across the Flask routes and the ingestion schema mapping.
- The data engine only handled a subset of normalized shop aliases, so shop-specific schema lookup could fail for some git -related values.
- This caused the Transaxle shop page to render inconsistently or fail to resolve the correct schema context.

## Files inspected
- app.py
- data_engine.py
- templates/index.html
- templates/admin.html
- templates/shop.html
- models.py

## Fix implemented
- Added more tolerant shop-code resolution in the ingestion path so both underscore-form UI codes and display-style names resolve correctly.
- Extended the schema alias table to include Press Shop and keep shop normalization consistent for future shop additions.
- Updated the admin and dashboard UI to expose Press Shop alongside the existing shops.
- Kept the workbook-based inspection flow intact and read-only, without introducing station-level uploads or AI extraction.

## Validation
- Verified that the Python modules parse successfully with:
  - /usr/bin/python3 -m py_compile app.py data_engine.py models.py taxonomy.py logger.py database.py competency_engine.py heuristic_engine.py graph_engine.py search_engine.py api_contracts.py reingest.py
- Verified editor diagnostics reported no errors in app.py or data_engine.py.

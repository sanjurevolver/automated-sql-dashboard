# Native Power BI validation — 19 November 2026

- Created a feature branch from merged `main`; no direct pushes to `main`.
- Generated native PBIP/PBIR report: four pages, five tables, three relationships and fourteen DAX measures. Power BI Desktop saved the validated semantic model as TMDL; that is the checked-in model format.
- Validated 45 report JSON files against Microsoft JSON schemas, including recursively resolved references.
- Opened the project successfully in the installed Power BI Desktop. The four report tabs, visuals and model tables appeared without a report-format error.
- Completed the full data refresh after the user handled the source privacy dialog.
- Ran a DAX reconciliation query inside Power BI Desktop. Results: 1,056,320 transactions; 4,500 accounts; gross 6,257,793,560.30 CZK; net 197,151,288.70 CZK; 2,999 negative-balance events. All matched the warehouse.
- Visually inspected all four report pages. Counts display without abbreviation, and the monthly charts use a continuous date axis spanning the full period.
- Tested district-to-account drill-down and return to district level; related negative-balance KPI changed from 2,999 to 571 for district 1, then returned to 2,999.
- Tested the 1993 slicer on Transaction Review and verified it propagated to Executive Overview. The filtered 28,205 transactions and 1,139 accounts matched a warehouse SQL query. Cleared the test filter before saving.
- Saved `BankingOperations/BankingOperations.pbix` from Power BI Desktop with imported data (approximately 29 MB). It remains local, excluded from Git. The editable PBIP/PBIR/TMDL source is submitted for PR review.
- Local automated validation: 16 passed, one PostgreSQL-only test skipped. Tests cover model bindings, layout bounds, date/money contracts and preserving Desktop-authored TMDL during regeneration.

The saved report uses local CSV exports from the pipeline; it is not a live PostgreSQL connection or a published Power BI Service report. New clones must run the pipeline, set ProjectRoot, and refresh. Power BI Service publication and a refresh gateway are not configured. The local UI uses Indian number grouping, so the full transaction count appears as `10,56,320`.

References: [Power BI project format](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview), [report definitions](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report), [semantic model definitions](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset).

# Native Power BI dashboard

Open `BankingOperations/BankingOperations.pbip` in Power BI Desktop. This is an editable native Power BI project containing the semantic model, DAX measures, relationships and four report pages. Refresh loads the exported tables from the latest successful pipeline run. It does not require a local PostgreSQL installation.

On the build machine, `BankingOperations/BankingOperations.pbix` is also saved with imported data and is ready to open. The four pages, exact DAX totals, synchronized year slicers and district/account drill-down were verified in Desktop. The PBIX is excluded from Git; clones use the editable project and refresh their own data.

## Run and refresh

1. Run `python pipeline.py` from the repository root if no successful run exists.
2. Run `python powerbi/build_project.py` once after cloning to set the `ProjectRoot` Power Query parameter to your checkout. Close the project in Desktop before regenerating. The generator replaces report layout definitions but preserves the existing Desktop-authored TMDL model, updating only its ProjectRoot expression. Preserve any manual layout edits first. Alternatively change ProjectRoot through Power Query's Manage Parameters.
3. Open `BankingOperations/BankingOperations.pbip`, dismiss the Home screen if it remains open, then click **Refresh**.
4. If Power BI asks for privacy levels, choose the appropriate classification for your local sources. Keep privacy checking enabled. The report only reads local pipeline outputs; it does not publish data to Power BI Service.
5. The `ReportDirectory` expression reads `reports/latest.json`, so future refreshes follow the latest successful run rather than a hardcoded run timestamp. Refresh captures current exports; it does not itself run the ETL.
6. Use **File → Save As → Power BI files (*.pbix)** to save a portable package with imported data. PBIX files and `.pbi` caches stay local and are excluded from Git. The text project is the reviewable source.

## Native report pages

| Page | Content | Interaction |
|---|---|---|
| Executive Overview | Four KPI cards, monthly credit/debit/net flow, operation volume | Synchronized year, district, direction and operation slicers; chart cross-filtering |
| Account Operations | District/account matrix, negative-balance count, volume per active account trend | Expand district rows to account level; matrix selections filter the page |
| Transaction Review | Ledger with IDs, dates, signed amounts, balances and review flags | Shared slicers plus searchable account selection |
| Quality & Methodology | Latest quality report, definitions and limitations | Quality metrics remain independent of financial slicers |

The model uses fixed-decimal monetary columns, a marked contiguous calendar, single-direction dimension relationships, and explicit measures. Raw balances are intentionally not summarized by default. The 100,000 CZK flag matches the pipeline; zero source rows currently meet that threshold.

`build_project.py` generates the layout using Microsoft's documented PBIP/PBIR formats and preserves the checked-in TMDL semantic model. A clean bootstrap without an existing model uses TMSL, which Desktop can convert to TMDL. Do not keep both model.bim and a TMDL definition folder in one semantic model. `tests/test_powerbi.py` checks field bindings, relationship endpoints, canvas bounds, money/date contracts and TMDL preservation. See `NATIVE_VALIDATION.md` for Desktop validation evidence.

## Optional direct PostgreSQL connection

The original `banking.pbids`, `measures.dax` and `theme.json` remain available for a separate PostgreSQL-connected build. They use warehouse table/column names, whereas the native project above exposes friendly model names. Do not paste their measures into the native model without translating those names.

1. Load PostgreSQL using `banking.pbids`, or import the four CSV tables in the latest successful report directory.
2. Import `dim_account`, `dim_date`, `dim_operation`, `fact_transaction`. For CSV imports, explicitly type IDs as whole number, amount/signed_amount/balance as fixed decimal, date_key/opened_date as Date, and high_value as True/False. Avoid automatic type inference for identifiers.
3. Create single-direction one-to-many relationships from dim_account.account_id, dim_date.date_key, dim_operation.operation to their matching fact columns. Disable Auto Date/Time. Mark dim_date as the date table using date_key. Do not relate aggregate views to the fact table.
4. Add measures from measures.dax individually. Format money as CZK, percentage measures as percentages. DATEADD requires the marked contiguous date table.
5. Import theme.json. Use a 16:9 canvas and the following pages:

| Page | Visuals | Interaction |
|---|---|---|
| Executive overview | Count, accounts, gross volume, net flow cards; monthly inflow/outflow line; operation bars | Date range, district and direction slicers |
| Account operations | District > account hierarchy matrix with count, net flow, negative balance events; volume per active account trend | Drill down district to account; drillthrough on account_id |
| Transaction review | Date, account, operation, amount, balance, purpose, high-value flag table | Account drillthrough, operation and high-value slicers; back button |
| Quality & methodology | Quality counts from quality.json; source attribution, threshold and statistical caveats | Link to run artifacts |

6. Use explicit Year > Quarter > Month > date_key hierarchy from dim_date, not month alone (which mixes years). Synchronize date/district slicers. Month names, if added, must sort by month number.
7. Validate unfiltered card totals against kpis.json. Compare each month to monthly_cashflow.csv, including credits minus debits = net flow. Verify filtering one account affects all visuals and drillthrough retains its ID.
8. Save as BankingOperations.pbix. For Power BI Service refresh from local PostgreSQL, configure an on-premises data gateway and credentials. Schedule refresh after the ETL completes; importing data does not itself refresh the service.

Official connector documentation: https://learn.microsoft.com/en-us/power-query/connectors/postgresql

Gross volume sums absolute ledger amounts. Net flow is credits minus debits. These are neither income nor profit. Balances are non-additive: never SUM balance across transactions to represent deposits. High-value flags use a fixed 100,000 CZK threshold and are not fraud outcomes.

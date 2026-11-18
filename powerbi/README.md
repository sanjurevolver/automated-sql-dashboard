# Power BI assembly and validation

These are connection, measure and layout assets, **not a completed PBIX report**. Power BI Desktop is required to assemble and save the native report; it was not available on the build machine.

1. Load PostgreSQL using `banking.pbids`, or import the four CSV tables in the latest successful report directory. PostgreSQL is the production path; CSV is an offline fallback.
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

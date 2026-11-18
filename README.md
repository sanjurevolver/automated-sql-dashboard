# Automated SQL-to-Dashboard Pipeline

Advanced financial transaction analytics using the historical PKDD'99 banking benchmark. Python ingestion → validated data → PostgreSQL star schema → SQL analytics and SciPy inference → Power BI. A DuckDB execution path and offline HTML dashboard make the project runnable without database or BI software.

## Quick start (Windows PowerShell)

```powershell
cd "$HOME\OneDrive\Desktop\PROJECT\automated-sql-dashboard"
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python pipeline.py
.\.venv\Scripts\python -m pytest -q
.\run.ps1
```

The environment created during development is already in `.venv`. The pipeline downloads the source once, verifies cached SHA-256 checksums, and runs all available local stages. `reports/latest.json` points to the latest successful run. Open that directory's `dashboard.html` in a browser; no web server or internet connection is needed. Each run also produces quality metrics, KPI totals, statistical results, findings, BI table exports and logs.

## PostgreSQL execution

Install PostgreSQL 17 locally, or Docker Desktop and use the included Compose file. Use a project-specific database, never an existing unrelated schema.

```powershell
$env:POSTGRES_PASSWORD = 'replace-with-a-local-password'
docker compose up -d
$env:DATABASE_URL = 'postgresql://analytics:replace-with-a-local-password@localhost:5432/analytics'
.\.venv\Scripts\python pipeline.py --backend postgres
```

URL-encode special characters in the DSN password. `.env.example` is a guide, not an automatically loaded secret. PostgreSQL writes use COPY staging, one database transaction, primary/foreign keys, check constraints, advisory locking and conflict-aware upserts. The included GitHub Actions job runs the same integration test on PostgreSQL 17. Local tests skip PostgreSQL unless `TEST_DATABASE_URL` points to an **empty disposable test database**. Do not set that variable to production.

## Implementation map

| Phase | Deliverables | Execution status |
|---|---|---|
| 1 · Ingestion | Version-pinned public CSV source; retries, timeouts, atomic download, checksums, manifest | Local runnable pipeline |
| 2 · Quality | Required-field rules, FK/date/money checks, duplicate handling, quarantine, configurable quality gate | Covered by automated tests |
| 3 · SQL | Star schema, account/date/operation dimensions, analytical views, cohorts, rolling windows, ranking, query-plan example | DuckDB execution; PostgreSQL integration requires server |
| 4 · Analysis | Distribution summaries, account-level Spearman correlation, paired Wilcoxon test, bootstrap interval, findings | Generated from warehouse population |
| 5 · Power BI | Connection file, DAX measures, theme, relationships and report specification | Native PBIX assembly still required; HTML preview generated |
| 6 · Delivery | Prefect flow, retries, scheduled deployment entry point, run logs, CI, documentation | Schedule registration and GitHub publication require services/authentication |

See [powerbi/README.md](powerbi/README.md) for report construction, slicers, drillthrough, reconciliation and refresh setup. The HTML preview is not a Power BI file.

## Data contract and loading semantics

- Grain: one transaction per `trans_id`. Currency: nominal Czech koruna (CZK). `PRIJEM` maps to credit; `VYDAJ` and `VYBER` map to debit.
- Missing categorical fields become `UNKNOWN`; missing money, IDs or dates are never imputed. Negative balances remain valid observations. Large amounts are retained and flagged at 100,000 CZK.
- Exact source duplicates collapse; conflicting duplicate IDs are quarantined. By default **any rejected row fails the batch before warehouse changes**. Review quarantine before explicitly setting `--max-reject-rate`.
- Incremental loading is snapshot reconciliation by transaction ID, not a date watermark: newly arriving older records and corrections are supported. Matching file fingerprints skip loading; changed files upsert inserts and corrections. Reprocessing after a report failure safely regenerates reports.
- The source is a static historical snapshot, not a live API. There is no new data to invent each day. `--source-dir <folder>` accepts replacement/incremental semicolon files named `account.asc` and `trans.asc`, with the same schema and complete referenced account dimension rows.
- Rows absent from a later file are not deleted. Source deletes need an explicit future tombstone contract. Account dimensions are Type 1: updated attributes replace prior values, not historical SCD2 tracking.
- Monetary storage is `DECIMAL(18,2)`. Parsing rejects sub-cent values. Python analysis uses floating-point summaries; database constraints protect signed-value reconciliation.
- Fingerprints include both input hashes and transformation contract version. Increase that version for semantic transformation changes. A cache checksum protects integrity after retrieval, not independent authenticity; record provenance accompanies downloaded files.
- Daily analytics use a contiguous calendar. Monthly rolling windows mean three observed months. No account-level end-of-day balance is inferred because posting order within a day is unavailable.

## Automation and recovery

```powershell
# Run once under Prefect:
.\.venv\Scripts\python flows.py
# For a persistent local deployment, start a local server in a separate terminal:
$env:PREFECT_HOME = "$PWD\.prefect"
.\.venv\Scripts\prefect server start
# In the serving terminal:
$env:PREFECT_API_URL = 'http://127.0.0.1:4200/api'
$env:WAREHOUSE_BACKEND = 'duckdb' # or postgres with DATABASE_URL set
.\.venv\Scripts\python flows.py --serve
```

The deployment uses 07:00 Asia/Kolkata, with two retries 30 seconds apart. The serving process and computer must remain running. This code defines a project scheduler; it is not an installed desktop reminder or proof that a schedule is active. Prefect UI exposes failed flow states; outbound alerts are not configured. Logs do not print connection credentials. Reports use UTC run timestamps.

Recovery: inspect the failing run's quality/quarantine files, fix the source or configuration, then rerun the same command. Failed database loads roll back both rows and the batch marker. Report-generation failures can occur after a successful warehouse commit; the next run skips the committed batch and regenerates analysis. `latest.json` advances only after all reports succeed. DuckDB uses its file writer lock; PostgreSQL serializes loads with an advisory lock.

## Analytical limitations

Gross volume is the sum of transaction amounts, not revenue. Net flow is credits minus debits, not profit. Bank balances are non-additive across transactions. High-value events are review candidates, not fraud labels. The account-level paired statistical comparison is exploratory and assumes accounts are independent; shared ownership can violate that assumption. No inference about current customers or modern financial risk is warranted.

## Provenance

Dataset: PKDD'99 Discovery Challenge, prepared by Petr Berka and Marta Sochorova. Original documentation: https://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm . Download mirror: https://github.com/jlacko/berka-dataset , revision `77e9972a1ead107e5f42a91dd989d587bff055a4`. This is a public historical banking benchmark; descriptions of its anonymization/synthetic provenance vary across catalogs, so treat it as a benchmark rather than verified production banking data. Raw data is downloaded separately and excluded from Git; no redistribution license is assumed.

Technology references: [Prefect scheduling](https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules) and [Microsoft PostgreSQL connector](https://learn.microsoft.com/en-us/power-query/connectors/postgresql).

## Portfolio publishing

The source includes a CI workflow; raw data, environments, local reports and secrets are excluded. Once GitHub authentication works:

```powershell
gh auth login
git init -b main
git add .
git commit -m "Build financial SQL-to-dashboard analytics pipeline"
gh repo create automated-sql-dashboard --public --source . --remote origin --push
```

Review tracked files before publishing. A successful local run is not evidence that PostgreSQL CI, Power BI rendering, a scheduled deployment, or GitHub publication has completed. Refer to `VALIDATION.md` for the actual build-session results.

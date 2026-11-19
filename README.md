# Automated Satellite Constellation Tracking & Conjunction Screening Pipeline

Real-time satellite constellation tracking and automated orbital conjunction screening. Python ingestion → Celestrak GP/OMM data → PostgreSQL star schema → SGP4 orbital propagation & KD-tree conjunction screening → Power BI.

## Operator derivation

Celestrak GP records do not provide an explicit satellite operator attribute. The ingestion layer applies a deterministic prefix and pattern mapping table against `OBJECT_NAME`. Any satellite with no matching rule strictly receives `operator = 'unknown'`:

| Match Pattern (`OBJECT_NAME` Prefix / Token) | Assigned `operator` | Notes |
|---|---|---|
| `^STARLINK.*` | `SpaceX` | Commercial mega-constellation |
| `^ONEWEB.*` | `Eutelsat OneWeb` | Commercial LEO constellation |
| `^(FLOCK\|SKYSAT\|DOVE\|PLANET).*` | `Planet Labs` | Earth observation constellation |
| `^IRIDIUM.*` | `Iridium Communications` | Satellite communications |
| `^GLOBALSTAR.*` | `Globalstar` | Satellite communications |
| `^(O3B\|SES\|ASTRA).*` | `SES` | Medium/geostationary fleet |
| `^(INTELSAT\|GALAXY).*` | `Intelsat` | Geostationary fleet |
| `^(BEIDOU\|CZ-\|FENGYUN\|YAOGAN\|TIANZHOU\|TIANGONG\|SHENZHOU).*` | `CNSA` | China National Space Administration / State |
| `^(COSMOS\|GLONASS\|SOYUZ\|PROGRESS).*` | `Roscosmos` | Russian state & military systems |
| `^(GPS\|NAVSTAR\|USA \|USA-).*` | `USSF` | United States Space Force / DoD |
| `^(GALILEO\|SENTINEL\|METOP\|ENVISAT).*` | `ESA` | European Space Agency / EUMETSAT |
| `^(ISS\|ZARYA\|NAUKA).*` | `Multinational` | International Space Station payload |
| *No match* | `unknown` | Explicit fallback (no guessing) |

## Object type derivation (two-stage)

The `object_type` column (`payload`, `debris`, `rocket_body`, `unknown`) is assigned using an authoritative two-stage classification:

1. **Stage 1 (Authoritative Group Classification)**:
   - `active`, `stations`, `starlink`, `oneweb` &rarr; `payload`
   - `*-debris`, `debris` &rarr; `debris`
   - Otherwise &rarr; `unknown`
2. **Stage 2 (`OBJECT_NAME` Refinement)**:
   - Contains `'R/B'`, `'ROCKET BODY'`, `'AKM'`, or starts with `'CZ-'` &rarr; `rocket_body`
   - Contains `' DEB '` as a standalone token (not `'DEBRIS'`) &rarr; `debris`
   - Name is exactly `'OBJECT'`, `'UNKNOWN'`, or starts with `'OBJECT '` (e.g. `'OBJECT A'`) &rarr; `unknown`
   - Otherwise &rarr; retain Stage 1 value
3. **Quarantine Rule**:
   - If Stage 1 assigns `'payload'` but Stage 2 resolves to `'rocket_body'` or `'debris'`, the record is quarantined to `quarantine.csv` with reason `group_name_conflict`.

## Cross-group deduplication precedence

When the same `NORAD_CAT_ID` appears in multiple ingested groups (e.g., in both `active` and `debris`), the pipeline resolves conflicts using strict deterministic precedence:
1. **Object Type Rank**: `debris` (4) > `rocket_body` (3) > `payload` (2) > `unknown` (1). Debris classifications are authoritative for orbital safety and conjunction screening.
2. **Operator Resolution**: Known operator attribution takes precedence over `unknown`.
3. **Epoch Freshness**: The record with the most recent observation `epoch_utc` is chosen.

Tiebreakers apply lexicographically in that exact order: object_type rank first, then known operator over unknown, then newest epoch.

## Quality gate and quarantine threshold

The pipeline sets a default `max_reject_rate = 0.01` (1.0% maximum allowable rejection rate). In a live satellite catalog of 20,000–30,000 objects, external catalog feeds regularly contain small single-digit anomalies (0.01%–0.05% rate) such as newly tracked rocket bodies temporarily tagged in active groups before decay or reclassification. Setting the default threshold to 1% provides operational resilience so scheduled background runs (such as the 6-hour Prefect cron) do not abort on minor upstream classification anomalies, while strictly catching bulk corruption or format breaking changes (> 1%). All rejected rows are preserved in `quarantine.csv` with granular error reasons.

## Provenance and composite checksums

- **Granular Provenance (`dim_snapshot_source.file_checksum`)**: Stores the direct SHA-256 digest of each raw group JSON payload downloaded from Celestrak.
- **Composite Provenance (`dim_snapshot.source_file_checksum`)**: A canonical rolling hash computed over all ingested group files using a deterministic sort-concat-hash algorithm. The string `f"{group_name}:{file_checksum}"` is constructed for every group, sorted lexicographically, joined by newlines (`\n`), and hashed via SHA-256:
  $$\text{source\_file\_checksum} = \text{SHA-256}\left(\text{join}\left(\text{"\\n"}, \left[g \parallel \text{":"} \parallel \text{checksum}_g \text{ for } g \in \text{sorted}(\text{groups})\right]\right)\right)$$

## Coordinate frames and propagation contract

- **Inertial Frame (TEME)**: `position_x/y/z` (ECI km) and `velocity_x/y/z` (ECI km/s) are natively produced by SGP4 in the **True Equator, Mean Equinox (TEME)** frame of epoch. Standard Celestrak TLE/OMM orbit propagations evaluate in TEME rather than standard GCRS (J2000). Geodetic transformations rotate from TEME to ECEF (WGS-84) using Greenwich Mean Sidereal Time (GMST).
- **Per-Record Epoch Contract**: In Phase 2 propagation, SGP4 propagates each satellite strictly to its own individual observation `EPOCH` from the GP JSON record. `dim_snapshot.tle_epoch_min` and `tle_epoch_max` record catalog time bounds and are never used as a uniform propagation epoch.


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

For the native Power BI report, run `python powerbi/build_project.py` using the project environment, then open `powerbi/BankingOperations/BankingOperations.pbip` in Power BI Desktop and refresh. The editable project includes four report pages and a related semantic model. See [native report instructions](powerbi/README.md) and [Desktop validation status](powerbi/NATIVE_VALIDATION.md).

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
| 3 · SQL | Star schema, account/date/operation dimensions, analytical views, cohorts, rolling windows, ranking, query-plan example | Full dataset verified on DuckDB locally and PostgreSQL 17 in CI |
| 4 · Analysis | Distribution summaries, account-level Spearman correlation, paired Wilcoxon test, bootstrap interval, findings | Generated from warehouse population |
| 5 · Power BI | Native PBIP report, four pages, related semantic model, DAX measures, slicers and account matrix | Refreshed and verified in Desktop; PBIX saved locally; exact totals and interactions checked |
| 6 · Delivery | Prefect flow, retries, scheduled deployment entry point, run logs, CI, documentation | Published to GitHub; local daily Prefect deployment registered |

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

On the original build machine, the server and runner were started and the 07:00 Asia/Kolkata schedule registered. Inspect the local UI at http://127.0.0.1:4200 . After reboot, run `./start-automation.ps1` to start the project-local background processes again. It refuses to start if port 4200 is already in use. The script runs the DuckDB backend; use the manual commands below to select PostgreSQL. This is not a Windows startup service.

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

The deployment uses 07:00 Asia/Kolkata, with two retries 30 seconds apart. The serving process and computer must remain running. A new clone must register its own deployment. Prefect UI exposes failed flow states; outbound alerts are not configured. Logs do not print connection credentials. Reports use UTC run timestamps.

Recovery: inspect the failing run's quality/quarantine files, fix the source or configuration, then rerun the same command. Failed database loads roll back both rows and the batch marker. Report-generation failures can occur after a successful warehouse commit; the next run skips the committed batch and regenerates analysis. `latest.json` advances only after all reports succeed. DuckDB uses its file writer lock; PostgreSQL serializes loads with an advisory lock.

## Analytical limitations

Gross volume is the sum of transaction amounts, not revenue. Net flow is credits minus debits, not profit. Bank balances are non-additive across transactions. High-value events are review candidates, not fraud labels. The account-level paired statistical comparison is exploratory and assumes accounts are independent; shared ownership can violate that assumption. No inference about current customers or modern financial risk is warranted.

## Provenance

Dataset: PKDD'99 Discovery Challenge, prepared by Petr Berka and Marta Sochorova. Original documentation: https://sorry.vse.cz/~berka/challenge/pkdd1999/berka.htm . Download mirror: https://github.com/jlacko/berka-dataset , revision `77e9972a1ead107e5f42a91dd989d587bff055a4`. This is a public historical banking benchmark; descriptions of its anonymization/synthetic provenance vary across catalogs, so treat it as a benchmark rather than verified production banking data. Raw data is downloaded separately and excluded from Git; no redistribution license is assumed.

Technology references: [Prefect scheduling](https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules) and [Microsoft PostgreSQL connector](https://learn.microsoft.com/en-us/power-query/connectors/postgresql).

## Portfolio publishing

Published repository: https://github.com/sanjurevolver/automated-sql-dashboard . The source includes a CI workflow; raw data, environments, local reports and secrets are excluded. To publish a separate clone under your own account:

```powershell
gh auth login
git init -b main
git add .
git commit -m "Build financial SQL-to-dashboard analytics pipeline"
gh repo create automated-sql-dashboard --public --source . --remote origin --push
```

Review tracked files before publishing. A successful local run is not evidence that PostgreSQL CI, Power BI rendering, a scheduled deployment, or GitHub publication has completed. Refer to `VALIDATION.md` for the actual build-session results.

# Satellite validation — Phase 3

## Phase 3A accepted baseline

- Local pinned snapshot fetched 2026-11-19 18:50:09–18:50:12 UTC: 30,820 source rows, 19,591 accepted unique objects, seven quarantined.
- Composite semantic source checksum: `4848ac0722c7e7e25923fee3d98bf74889443894aab28be2f9c47d85bd84eacb`.
- 2,210,997 candidate pairs; sustained >=2: 186,833; sustained >=3: 62,977.
- Measured seconds: propagation 30.005; screening/aggregation 112.923; refinement initialization 1.104; refinement 559.218; total 703.250.
- All-candidate retention and local +/-30 s refinement accepted; altitude sieve dropped.
- CI: 28 passed, including PostgreSQL tests: https://github.com/sanjurevolver/automated-sql-dashboard/actions/runs/44593141655 .

## Phase 3B integration

Implementation adds locked bulk COPY plus a set-based INSERT, durable screening completion, retry recovery, snapshot bootstrap and compact summary outputs. Full-dataset CI measurement is pending; no estimated COPY timing or unsupported single-snapshot interval is reported here.

## Historical banking validation (superseded domain)

The following is retained as a historical build record, not the current satellite execution or deployment status.

# Validation record — 18 November 2026

This records the initial pipeline build. For the subsequent native Power BI project and its current Desktop validation status, see [powerbi/NATIVE_VALIDATION.md](powerbi/NATIVE_VALIDATION.md).

## Completed

- Local Python 3.13 environment installed; exact Windows package versions recorded in requirements-lock.txt. Use requirements.txt for cross-platform installation.
- Downloaded both source files from a pinned public mirror revision; SHA-256 checksums recorded in data/raw/manifest.json.
- Loaded 1,056,320 transactions and 4,500 accounts into local DuckDB. Coverage: 1993-01-01 through 1998-12-31.
- Accepted all source rows; zero rejected rows or duplicates. Retained 183,114 unspecified operations and 2,999 negative-balance observations. The 100,000 CZK threshold flags zero rows in this snapshot; it must not be presented as detected fraud.
- Exact warehouse gross volume: 6,257,793,560.30 CZK. Net ledger flow: 197,151,288.70 CZK.
- Repeated local execution skipped the existing fingerprint, added zero rows, and regenerated reports.
- Local automated suite: 13 passed, PostgreSQL-only integration skipped locally.
- GitHub CI: **14 passed**, including PostgreSQL inserts, corrections, late arrivals, duplicate-safe retries, failed-batch rollback and SQL aggregate reconciliation.
- Full PostgreSQL 17 execution: all 1,056,320 rows loaded; exact Decimal totals reconciled to the values above; repeat execution skipped the batch with zero additions.
- CI evidence: https://github.com/sanjurevolver/automated-sql-dashboard/actions/runs/44366629281 . Artifact `analytics-validation` contains run, quality, KPI and statistical evidence.
- Generated offline interactive HTML dashboard, Power BI CSV exports, distribution/correlation tables, paired account statistics and written insights.
- Prefect module imported successfully. Local background server and runner started. Deployment `sql-to-dashboard/banking-daily` registered, schedule active, cron `0 7 * * *`, timezone `Asia/Kolkata`.

## Remaining limitations

- Power BI Desktop was not installed. Connection file, DAX, theme, model relationships and page specifications are supplied; no native PBIX report has been assembled or visually validated.
- Browser policy rejected local-file preview access. HTML was generated successfully, but visual browser QA was not completed.
- PostgreSQL is verified in disposable GitHub CI; the persistent local warehouse is DuckDB. Install local PostgreSQL or Docker to maintain a local PostgreSQL warehouse.
- Prefect processes require the computer to remain on and must be restarted after reboot. No external notification destination or Power BI Service refresh gateway is configured.
- Notebook supplied for interactive exploration; its cells were not executed as a Jupyter notebook. Equivalent analysis code ran in the pipeline.
- Query indexes and EXPLAIN ANALYZE examples are supplied; no benchmarked performance-improvement claim is made.

## Statistical result

For 4,500 paired accounts, mean credit size minus mean debit size averages 3,129.89 CZK (account bootstrap 95% interval 3,048.25–3,213.81 CZK). The paired Wilcoxon p-value underflows floating-point precision; it is not literally zero probability. This historical observational comparison is not causal and assumes account independence.

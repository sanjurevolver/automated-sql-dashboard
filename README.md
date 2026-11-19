# Automated Satellite Constellation Tracking & Conjunction Screening Pipeline

Snapshot-based satellite constellation tracking and automated orbital conjunction screening. Python ingestion → Celestrak GP/OMM data → PostgreSQL star schema → SGP4 orbital propagation & KD-tree conjunction screening → Power BI.

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

The pipeline sets a default `max_reject_rate = 0.01` (1.0% maximum allowable rejection rate). In a live satellite catalog of 20,000–30,000 objects, external catalog feeds regularly contain small single-digit anomalies (0.01%–0.05% rate) such as newly tracked rocket bodies temporarily tagged in active groups before decay or reclassification. Setting the default threshold to 1% provides operational resilience so scheduled background runs (when orchestration is enabled in a later phase) do not abort on minor upstream classification anomalies, while strictly catching bulk corruption or format breaking changes (> 1%). All rejected rows are preserved in `quarantine.csv` with granular error reasons.

## Provenance and composite checksums

- **Granular Provenance (`dim_snapshot_source.file_checksum`)**: Stores SHA-256 of canonical JSON serialization (`json.dumps(records, sort_keys=True)`). The download manifest separately stores the SHA-256 of the original response bytes; these are different contracts.
- **Composite Provenance (`dim_snapshot.source_file_checksum`)**: A canonical rolling hash computed over all ingested group files using a deterministic sort-concat-hash algorithm. The string `f"{group_name}:{file_checksum}"` is constructed for every group, sorted lexicographically, joined by newlines (`\n`), and hashed via SHA-256:
  $$\text{source\_file\_checksum} = \text{SHA-256}\left(\text{join}\left(\text{"\\n"}, \left[g \parallel \text{":"} \parallel \text{checksum}_g \text{ for } g \in \text{sorted}(\text{groups})\right]\right)\right)$$

## Coordinate frames and propagation contract

- **Inertial State Vectors (TEME)**: Both `position_x/y/z` (ECI km) and `velocity_x/y/z` (ECI km/s) stored in `fact_orbital_position` are preserved strictly in the **True Equator, Mean Equinox (TEME)** quasi-inertial frame of epoch produced natively by SGP4. Retaining both position and velocity in the common TEME frame ensures that Phase 3 conjunction screening computes relative velocity ($\Delta v = \|\mathbf{v}_A - \mathbf{v}_B\|$) and miss distances consistently without rotating-frame fictitious force distortions.
- **Geodetic Altitude & GCRS Differences**: Geodetic latitude, longitude, and ellipsoidal altitude (`lat_deg`, `lon_deg`, `altitude_km`) are derived by rotating TEME state vectors to ECEF (WGS-84) via Greenwich Mean Sidereal Time (GMST) and applying Bowring's closed-form algorithm. This approximation uses UTC in place of UT1 and omits polar motion; it is not a precision Earth-orientation transformation.
- **Longitude Range**: `lon_deg` is computed using `atan2(y, x)` and strictly mapped to the interval **$[-180.0^\circ, +180.0^\circ]$** for native compatibility with Power BI map/globe visual layers (e.g. Azure Maps).
- **Per-Record Epoch Contract**: SGP4 propagates each satellite strictly to its own individual observation `EPOCH` from the GP JSON record. `dim_snapshot.tle_epoch_min` and `tle_epoch_max` record catalog span bounds for audit provenance for the stored orbital facts. The screening engine separately uses `tle_epoch_max` as its common forward-propagation start.
- **OMM Record Normalization (`normalize_omm_record`)**: Provides a non-mutating default-value fill for optional CCSDS OMM fields (`CLASSIFICATION_TYPE='U'`, `EPHEMERIS_TYPE=0`, `ELEMENT_SET_NO=999`, etc.) to support synthetic test stubs. It never mutates `EPOCH`, never renames fields, and never performs unit conversions on live Celestrak records.
- **Upstream Catalog Drift**: Small differences in unique satellite counts between snapshot runs (e.g., 19,594 at 11:04 UTC vs. 19,590 at 17:10 UTC) reflect natural upstream catalog updates as low Earth orbit debris objects decay and are deregistered by space tracking authorities.
- **Celestrak Debris Endpoints**: Celestrak does not support a generic `GROUP=debris` query parameter (which returns `Invalid query: ...`). Debris catalogs are queried by specific breakup event groups (`cosmos-2251-debris`, `iridium-33-debris`, `fengyun-1c-debris`). Any unresolvable group URL is logged in `manifest.json` with `fetch_status: "unavailable_or_invalid"` without aborting the batch.


## Running the satellite pipeline

Use Python 3.13 and PostgreSQL 17 with a dedicated project database. Set `DATABASE_URL` in your environment, install `requirements.txt`, then run:

```powershell
.\.venv\Scripts\python.exe pipeline.py --source-dir data/raw/gp
.\.venv\Scripts\python.exe -m pytest -v
```

Omit `--source-dir` to use the existing cached-download path. It requests six valid groups: active, starlink, stations, cosmos-2251-debris, iridium-33-debris, and fengyun-1c-debris. Starlink and stations overlap active; cross-group counts are not additive unique-object counts. Never use generic GROUP=debris. Keep the JSON files and manifest together. Raw data, CSV spools, generated reports, environments and credentials remain excluded from Git.

`reports/latest.json` points to the latest successful report directory. Outputs include `quality.json`, `screening.json`, `statistics.json`, `conjunctions_per_snapshot.csv`, and `risk_tier_distribution.csv`. The large `conjunctions.csv` spool is local evidence and is not uploaded to GitHub. CI uploads raw inputs plus manifest as `raw-gp-data` and compact outputs as `analytics-validation`.

## Screening Engine

The common start is `dim_snapshot.tle_epoch_max`. SGP4 propagates all accepted objects in TEME over a half-open 24-hour horizon, at 60-second intervals (1,440 samples). cKDTree screens all objects at 100 km; there is no altitude or apogee/perigee sieve. All unique coarse candidates are retained by default. Sustained >=2 or >=3 consecutive-step filters are experimental opt-ins because they discard brief approaches.

For each canonical pair, golden-section minimization refines the best coarse sample in a local +/-30-second window, with a 1 ms time convergence tolerance. It reports minimum distance and relative TEME speed at that numerical TCA. This is local refinement, not a guaranteed global minimum across every encounter in a day. CRITICAL means <10 km, HIGH [10,50) km, and MEDIUM [50,100) km. These are proximity labels, not collision probabilities.

The accepted Phase 3A benchmark used 19,591 objects: 2,210,997 unique coarse pairs, 186,833 sustained >=2 pairs, and 62,977 sustained >=3 pairs. Measured stages were 30.005 s propagation, 112.923 s screening/aggregation, 1.104 s refinement initialization and 559.218 s refinement, totaling 703.250 s. Different catalogs and machines produce different measurements. Reproduce against a pinned snapshot with `python scripts/benchmark_conjunction.py`; it checks file hashes before and after and never downloads.

## Bulk load, transactions and recovery

Catalog ingestion keeps its existing fingerprint and advisory-lock loader. Screening resolves the persisted snapshot by semantic source checksum, rather than the new fetch-time snapshot ID created on a retry. A `screening_batch` row distinguishes a completed zero-conjunction result from a snapshot not yet screened.

Expensive propagation and refinement occur outside the write transaction. The loader then acquires the same PostgreSQL transaction advisory lock (734821), rechecks completion, streams the spool in 1 MiB blocks through `COPY ... FROM STDIN`, and performs one set-based INSERT. Facts and completion marker commit together. Measurements distinguish COPY-to-staging time, indexed/foreign-key-checked INSERT time, and full transaction wall-clock including commit. The [psycopg COPY documentation](https://www.psycopg.org/psycopg3/docs/basic/copy.html) describes block streaming and transaction behavior.

A successful rerun skips both ingestion and screening and regenerates analysis. A failed screening or COPY leaves no completed screening marker; the next run can skip committed ingestion and retry screening. Invalid rows or duplicate pairs roll back the whole conjunction load. Concurrent writers recheck completion under the lock, so only one commits a given snapshot. Unfinished results can be replaced atomically; completed contracts are immutable and a changed engine contract requires an explicit migration. Report-generation failure does not invalidate committed facts; `latest.json` advances only after reporting succeeds.

## Statistical summaries and limitations

Summaries count unique pairs per completed snapshot and risk tier. Zero-count tiers are explicit; incomplete snapshots are excluded from statistical inference. Rates use pairs per 24 hours (count divided by horizon hours, multiplied by 24).

The 95% percentile bootstrap resamples whole snapshots with replacement (10,000 draws, seed 42), preserving tier dependence within each snapshot. A single completed snapshot reports its observed rates and `insufficient_snapshots` with null bounds: it cannot estimate between-snapshot uncertainty. Tests use synthetic multiple-snapshot data only to verify the calculation, not as real catalog evidence. Adjacent snapshots share objects and overlapping forecast windows, so even multiple-snapshot intervals are exploratory; collect comparable, well-separated snapshots before interpreting them inferentially.

TLE/GP extrapolation error grows with element age and depends on orbit, atmospheric drag, maneuvers and observation quality. BSTAR models drag, but a universal 1-5 km/day error guarantee is not justified for every LEO object; this pipeline has no precise ephemeris or covariance to estimate such an error bound. See [CelesTrak's element-set discussion](https://celestrak.org/columns/v04n03/) and [NASA's TLE/SGP4 discussion](https://ntrs.nasa.gov/api/citations/20250010561/downloads/20250010561.pdf).

A 100 km threshold is a screening choice, not an uncertainty envelope. At 60-second spacing, brief approaches can occur between samples without entering the coarse radius. Refining only the best coarse sample per pair can miss a smaller minimum in another encounter window. The first +/-30-second refinement window can extend before the start; the last sample is +23:59 and its window ends at +23:59:30. These accepted Phase 3A limitations remain explicit. Epochs may be stale or later than fetch time; the maximum catalog epoch is used as specified, not silently replaced with wall-clock time. Changes to horizon, threshold, cadence or filters require a new benchmark and contract review.

## Delivery status

Phase 3 covers screening, PostgreSQL loading and statistical summaries. Phase 4 Power BI and scheduling work is not implemented by this change. Legacy banking Power BI artifacts remain in the repository for history; they do not visualize satellite results and are not part of the current execution path. See `VALIDATION.md` for measured evidence. Contributions use feature branches and pull requests; never push directly to main or merge without approval.

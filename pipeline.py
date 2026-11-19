"""Reproducible Celestrak GP ingestion, validation, and atomic PostgreSQL warehouse loading."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent

CELESTRAK_BASE_URL = 'https://celestrak.org/NORAD/elements/gp.php'
GROUPS = [
    'active',
    'starlink',
    'stations',
    'cosmos-2251-debris',
    'iridium-33-debris',
    'fengyun-1c-debris',
    'debris',
]

USER_AGENT = 'SatelliteTrackerPipeline/1.0 (AutomatedConjunctionScreening; automated-pipeline)'

SHELL_BANDS = [
    ('LEO-low', 160.0, 600.0),
    ('LEO-high', 600.0, 2000.0),
    ('MEO', 2000.0, 35500.0),
    ('GEO', 35500.0, 36000.0),
    ('HEO', 36000.0, 100000.0),
]

OPERATOR_PATTERNS = [
    (re.compile(r'^STARLINK.*', re.I), 'SpaceX'),
    (re.compile(r'^ONEWEB.*', re.I), 'Eutelsat OneWeb'),
    (re.compile(r'^(FLOCK|SKYSAT|DOVE|PLANET).*', re.I), 'Planet Labs'),
    (re.compile(r'^IRIDIUM.*', re.I), 'Iridium Communications'),
    (re.compile(r'^GLOBALSTAR.*', re.I), 'Globalstar'),
    (re.compile(r'^(O3B|SES|ASTRA).*', re.I), 'SES'),
    (re.compile(r'^(INTELSAT|GALAXY).*', re.I), 'Intelsat'),
    (re.compile(r'^(BEIDOU|CZ-|FENGYUN|YAOGAN|TIANZHOU|TIANGONG|SHENZHOU).*', re.I), 'CNSA'),
    (re.compile(r'^(COSMOS|GLONASS|SOYUZ|PROGRESS).*', re.I), 'Roscosmos'),
    (re.compile(r'^(GPS|NAVSTAR|USA |USA-).*', re.I), 'USSF'),
    (re.compile(r'^(GALILEO|SENTINEL|METOP|ENVISAT).*', re.I), 'ESA'),
    (re.compile(r'^(ISS|ZARYA|NAUKA).*', re.I), 'Multinational'),
]

def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding='utf-8')
    temp.replace(path)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def digest_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()

def derive_operator(name: str) -> str:
    if not name:
        return 'unknown'
    for pattern, operator in OPERATOR_PATTERNS:
        if pattern.match(name.strip()):
            return operator
    return 'unknown'

def derive_object_type(group: str, name: str) -> tuple[str, str | None]:
    """Two-stage object_type classification with authoritative group mapping and name refinement.
    
    Returns (object_type, quarantine_reason_or_none).
    """
    group_lower = (group or '').lower()
    if 'debris' in group_lower:
        stage1 = 'debris'
    elif any(k in group_lower for k in ['active', 'stations', 'starlink', 'oneweb']):
        stage1 = 'payload'
    else:
        stage1 = 'unknown'

    name_clean = (name or '').strip().upper()

    # Stage 2: Name refinement
    is_rb = (
        'R/B' in name_clean
        or 'ROCKET BODY' in name_clean
        or 'AKM' in name_clean
        or name_clean.startswith('CZ-')
    )
    is_deb = bool(re.search(r'(^|\s)DEB(\s|$)', name_clean))
    is_unknown = name_clean in {'OBJECT', 'UNKNOWN'} or bool(re.match(r'^OBJECT(\s+[A-Z0-9]+)?$', name_clean))

    if is_rb:
        stage2 = 'rocket_body'
    elif is_deb:
        stage2 = 'debris'
    elif is_unknown:
        stage2 = 'unknown'
    else:
        stage2 = stage1

    quarantine_reason = None
    if stage1 == 'payload' and stage2 in ('rocket_body', 'debris'):
        quarantine_reason = 'group_name_conflict'

    return stage2, quarantine_reason

def download(refresh=False) -> Path:
    folder = ROOT / 'data/raw/gp'
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / 'data/raw/manifest.json'
    old_manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}

    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    session.mount(
        'https://',
        HTTPAdapter(
            max_retries=Retry(
                total=4,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=['GET'],
            )
        ),
    )

    metadata = dict(old_manifest)
    for group in GROUPS:
        target_file = folder / f'{group}.json'
        if target_file.exists() and not refresh:
            if group in old_manifest and old_manifest[group].get('sha256') == digest(target_file):
                metadata[group] = old_manifest[group]
                continue

        url = f'{CELESTRAK_BASE_URL}?GROUP={group}&FORMAT=json'
        temporary = target_file.with_suffix('.part')
        try:
            with session.get(url, stream=True, timeout=(15, 60)) as response:
                if response.status_code == 403 and b'not updated' in response.content:
                    logging.info("Celestrak group %s data has not updated since last fetch (HTTP 403).", group)
                    if target_file.exists():
                        metadata[group] = old_manifest.get(group, {
                            'url': url,
                            'retrieved_at': datetime.now(timezone.utc).isoformat(),
                            'sha256': digest(target_file),
                            'bytes': target_file.stat().st_size,
                            'fetch_status': 'cached_not_updated',
                        })
                    else:
                        metadata[group] = {
                            'url': url,
                            'retrieved_at': datetime.now(timezone.utc).isoformat(),
                            'fetch_status': 'rate_limited',
                        }
                    continue

                response.raise_for_status()
                with temporary.open('wb') as target:
                    for chunk in response.iter_content(1024 * 1024):
                        target.write(chunk)

                # Validate downloaded content is valid JSON array
                raw_bytes = temporary.read_bytes()
                try:
                    parsed = json.loads(raw_bytes)
                    if not isinstance(parsed, list):
                        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
                except Exception as json_err:
                    logging.warning("Group %s returned non-JSON response (%s). Logged in manifest; continuing.", group, json_err)
                    metadata[group] = {
                        'url': url,
                        'retrieved_at': datetime.now(timezone.utc).isoformat(),
                        'fetch_status': 'unavailable_or_invalid',
                        'error': str(json_err),
                    }
                    temporary.unlink(missing_ok=True)
                    continue

                checksum = digest(temporary)
                file_size = temporary.stat().st_size
                metadata[group] = {
                    'url': url,
                    'retrieved_at': datetime.now(timezone.utc).isoformat(),
                    'sha256': checksum,
                    'bytes': file_size,
                    'record_count': len(parsed),
                    'fetch_status': 'success',
                }
                temporary.replace(target_file)
        except Exception as exc:
            logging.warning("Failed to fetch group %s: %s. Continuing with available groups.", group, exc)
            metadata[group] = {
                'url': url,
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'fetch_status': 'failed',
                'error': str(exc),
            }
        finally:
            temporary.unlink(missing_ok=True)

    session.close()
    save_json(manifest_path, metadata)
    return folder

def clean(raw_records_by_group: dict[str, list[dict]], fetch_time: datetime | None = None):
    """Validate Celestrak GP records, perform 2-stage object_type classification, and quarantine conflicts."""
    if fetch_time is None:
        fetch_time = datetime.now(timezone.utc)

    all_rows = []
    source_count = 0
    snapshot_sources = []

    for group, records in raw_records_by_group.items():
        count = len(records) if records else 0
        source_count += count
        content_bytes = json.dumps(records, sort_keys=True).encode('utf-8')
        file_sha = digest_bytes(content_bytes)
        snapshot_sources.append({
            'group_name': group,
            'file_checksum': file_sha,
            'record_count': count,
            'fetch_status': 'success' if count > 0 else 'empty_or_failed',
        })
        for r in records or []:
            item = dict(r)
            item['_group'] = group
            all_rows.append(item)

    if not all_rows:
        empty_sat = pd.DataFrame(columns=['norad_id', 'object_name', 'operator', 'launch_designator', 'object_type'])
        empty_snap = pd.DataFrame(columns=['snapshot_id', 'tle_fetch_time', 'tle_epoch_min', 'tle_epoch_max', 'source_file_checksum'])
        empty_src = pd.DataFrame(columns=['group_name', 'file_checksum', 'record_count', 'fetch_status'])
        empty_rej = pd.DataFrame(columns=['norad_id', 'object_name', 'rejection_reason'])
        report = {'source_rows': 0, 'accepted_rows': 0, 'rejected_rows': 0}
        return empty_sat, empty_snap, empty_src, empty_rej, report

    df = pd.DataFrame(all_rows)
    reasons = pd.Series('', index=df.index)

    # 1. Validate NORAD_CAT_ID
    if 'NORAD_CAT_ID' not in df.columns:
        raise ValueError("Missing NORAD_CAT_ID in source records")
    norad_numeric = pd.to_numeric(df['NORAD_CAT_ID'], errors='coerce')
    invalid_norad = norad_numeric.isna() | (norad_numeric <= 0) | ((norad_numeric % 1) != 0)
    reasons.loc[invalid_norad] += 'invalid_norad_id;'
    df['norad_id'] = norad_numeric.fillna(-1).astype('int64')

    # 2. Validate OBJECT_NAME
    if 'OBJECT_NAME' not in df.columns:
        raise ValueError("Missing OBJECT_NAME in source records")
    invalid_name = df['OBJECT_NAME'].isna() | (df['OBJECT_NAME'].astype(str).str.strip() == '')
    reasons.loc[invalid_name] += 'invalid_object_name;'
    df['object_name'] = df['OBJECT_NAME'].astype(str).str.strip()

    # 3. Validate EPOCH
    if 'EPOCH' not in df.columns:
        raise ValueError("Missing EPOCH in source records")
    parsed_epoch = pd.to_datetime(df['EPOCH'], utc=True, format='ISO8601', errors='coerce')
    reasons.loc[parsed_epoch.isna()] += 'invalid_epoch;'
    df['epoch_utc'] = parsed_epoch

    # 4. Two-stage object_type derivation and conflict quarantine
    object_types = []
    for idx, row in df.iterrows():
        otype, conf_reason = derive_object_type(row['_group'], row['object_name'])
        object_types.append(otype)
        if conf_reason:
            reasons.loc[idx] += conf_reason + ';'
    df['object_type'] = object_types

    # 5. International Launch Designator
    df['launch_designator'] = df.get('OBJECT_ID', pd.Series('UNKNOWN', index=df.index)).fillna('UNKNOWN').astype(str).str.strip()
    df['operator'] = df['object_name'].apply(derive_operator)

    # 6. Duplicate NORAD ID conflict handling within the same source group
    # If the same NORAD ID appears with conflicting attributes within the same group, flag conflicting
    valid_mask = reasons == ''
    valid_df = df.loc[valid_mask].copy()
    grouped_attr = valid_df.groupby(['_group', 'norad_id'])[['object_name', 'launch_designator']].nunique()
    conflict_norads = grouped_attr[(grouped_attr['object_name'] > 1) | (grouped_attr['launch_designator'] > 1)].index
    for group_val, norad_val in conflict_norads:
        reasons.loc[(df['_group'] == group_val) & (df['norad_id'] == norad_val)] += 'conflicting_norad_id;'

    rejected = df.loc[reasons != ''].copy()
    rejected['rejection_reason'] = reasons[reasons != '']
    accepted = df.loc[reasons == ''].copy()

    # Deduplicate accepted satellites across groups with deterministic precedence:
    # 1. object_type: debris (4) > rocket_body (3) > payload (2) > unknown (1)
    # 2. operator: known operator > unknown
    # 3. epoch_utc: latest epoch wins
    type_priority = {'debris': 4, 'rocket_body': 3, 'payload': 2, 'unknown': 1}
    accepted['_type_rank'] = accepted['object_type'].map(type_priority).fillna(0).astype(int)
    accepted['_operator_known'] = (accepted['operator'] != 'unknown').astype(int)

    accepted_satellites = (
        accepted.sort_values(
            by=['norad_id', '_type_rank', '_operator_known', 'epoch_utc'],
            ascending=[True, False, False, False]
        )
        .drop_duplicates(subset=['norad_id'], keep='first')
        [['norad_id', 'object_name', 'operator', 'launch_designator', 'object_type']]
        .reset_index(drop=True)
    )

    # Snapshot summary: Canonical composite checksum of lexicographically sorted group_name:file_checksum
    sorted_sources = sorted(f"{s['group_name']}:{s['file_checksum']}" for s in snapshot_sources)
    combined_checksum = hashlib.sha256('\n'.join(sorted_sources).encode('utf-8')).hexdigest()
    snapshot_id = f"snap_{fetch_time.strftime('%Y%m%dT%H%M%S')}_{combined_checksum[:8]}"
    epoch_min = accepted['epoch_utc'].min() if not accepted.empty else fetch_time
    epoch_max = accepted['epoch_utc'].max() if not accepted.empty else fetch_time

    snapshot_df = pd.DataFrame([{
        'snapshot_id': snapshot_id,
        'tle_fetch_time': fetch_time,
        'tle_epoch_min': epoch_min,
        'tle_epoch_max': epoch_max,
        'source_file_checksum': combined_checksum,
    }])

    snapshot_sources_df = pd.DataFrame(snapshot_sources)

    quality = {
        'source_rows': source_count,
        'accepted_rows': len(accepted_satellites),
        'rejected_rows': len(rejected),
        'payload_count': int((accepted_satellites['object_type'] == 'payload').sum()),
        'debris_count': int((accepted_satellites['object_type'] == 'debris').sum()),
        'rocket_body_count': int((accepted_satellites['object_type'] == 'rocket_body').sum()),
        'unknown_count': int((accepted_satellites['object_type'] == 'unknown').sum()),
    }

    return accepted_satellites, snapshot_df, snapshot_sources_df, rejected, quality

@contextmanager
def warehouse(backend='postgres', database=None):
    if backend != 'postgres':
        raise ValueError("Only 'postgres' backend is supported in this production environment.")
    import psycopg
    dsn = database or os.environ.get('DATABASE_URL')
    if not dsn:
        raise ValueError('Set DATABASE_URL for PostgreSQL')
    con = psycopg.connect(dsn)
    try:
        yield con
    finally:
        con.close()

def frame(con, query, backend='postgres'):
    cur = con.execute(query)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

def stage(con, name, df):
    target_map = {
        's_satellite': 'dim_satellite',
        's_snapshot': 'dim_snapshot',
        's_snapshot_source': 'dim_snapshot_source',
    }
    target = target_map[name]
    cols = list(df.columns)
    col_str = ', '.join(f'"{c}"' for c in cols)
    con.execute(f'CREATE TEMP TABLE {name} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP')
    with con.cursor().copy(f'COPY {name} ({col_str}) FROM STDIN') as copy:
        for row in df.itertuples(index=False, name=None):
            copy.write_row(tuple(v.to_pydatetime() if isinstance(v, pd.Timestamp) else v for v in row))

def load(con, satellites, snapshot, snapshot_sources, fingerprint, quality, backend='postgres'):
    con.execute('BEGIN')
    try:
        con.execute('SELECT pg_advisory_xact_lock(734821)')
        con.execute((ROOT / 'sql/schema.sql').read_text(encoding='utf-8'))

        # Seed standard orbital shell bands if missing
        for shell_name, low_km, high_km in SHELL_BANDS:
            con.execute(
                """
                INSERT INTO dim_orbit_shell (shell_name, altitude_band_low_km, altitude_band_high_km)
                VALUES (%s, %s, %s)
                ON CONFLICT (shell_name) DO NOTHING
                """,
                (shell_name, low_km, high_km),
            )

        # Check existing batch
        if con.execute('SELECT 1 FROM etl_batch WHERE fingerprint = %s', (fingerprint,)).fetchone():
            con.execute((ROOT / 'sql/views.sql').read_text(encoding='utf-8'))
            con.commit()
            return {'status': 'skipped', 'new_rows': 0, 'updated_rows': 0}

        if satellites.empty:
            raise ValueError("No valid satellite records to load")

        # Stage data
        stage(con, 's_satellite', satellites)
        stage(con, 's_snapshot', snapshot)

        # Count new and updated satellites
        new_satellites = con.execute(
            """
            SELECT COUNT(*) FROM s_satellite s
            LEFT JOIN dim_satellite d USING (norad_id)
            WHERE d.norad_id IS NULL
            """
        ).fetchone()[0]

        updated_satellites = con.execute(
            """
            SELECT COUNT(*) FROM s_satellite s
            JOIN dim_satellite d USING (norad_id)
            WHERE d.object_name IS DISTINCT FROM s.object_name
               OR d.operator IS DISTINCT FROM s.operator
               OR d.launch_designator IS DISTINCT FROM s.launch_designator
               OR d.object_type IS DISTINCT FROM s.object_type
            """
        ).fetchone()[0]

        # Upsert dim_satellite
        con.execute(
            """
            INSERT INTO dim_satellite (norad_id, object_name, operator, launch_designator, object_type)
            SELECT norad_id, object_name, operator, launch_designator, object_type FROM s_satellite
            ON CONFLICT (norad_id) DO UPDATE SET
                object_name = EXCLUDED.object_name,
                operator = EXCLUDED.operator,
                launch_designator = EXCLUDED.launch_designator,
                object_type = EXCLUDED.object_type
            WHERE dim_satellite.object_name IS DISTINCT FROM EXCLUDED.object_name
               OR dim_satellite.operator IS DISTINCT FROM EXCLUDED.operator
               OR dim_satellite.launch_designator IS DISTINCT FROM EXCLUDED.launch_designator
               OR dim_satellite.object_type IS DISTINCT FROM EXCLUDED.object_type
            """
        )

        # Upsert dim_snapshot
        con.execute(
            """
            INSERT INTO dim_snapshot (snapshot_id, tle_fetch_time, tle_epoch_min, tle_epoch_max, source_file_checksum)
            SELECT snapshot_id, tle_fetch_time, tle_epoch_min, tle_epoch_max, source_file_checksum FROM s_snapshot
            ON CONFLICT (snapshot_id) DO UPDATE SET
                tle_fetch_time = EXCLUDED.tle_fetch_time,
                tle_epoch_min = EXCLUDED.tle_epoch_min,
                tle_epoch_max = EXCLUDED.tle_epoch_max,
                source_file_checksum = EXCLUDED.source_file_checksum
            """
        )

        # Retrieve snapshot_sk to link sources
        snap_id = snapshot.iloc[0]['snapshot_id']
        snapshot_sk = con.execute("SELECT snapshot_sk FROM dim_snapshot WHERE snapshot_id = %s", (snap_id,)).fetchone()[0]

        sources_with_sk = snapshot_sources.copy()
        sources_with_sk['snapshot_sk'] = snapshot_sk
        stage(con, 's_snapshot_source', sources_with_sk[['snapshot_sk', 'group_name', 'file_checksum', 'record_count', 'fetch_status']])

        con.execute(
            """
            INSERT INTO dim_snapshot_source (snapshot_sk, group_name, file_checksum, record_count, fetch_status)
            SELECT snapshot_sk, group_name, file_checksum, record_count, fetch_status FROM s_snapshot_source
            ON CONFLICT (snapshot_sk, group_name) DO UPDATE SET
                file_checksum = EXCLUDED.file_checksum,
                record_count = EXCLUDED.record_count,
                fetch_status = EXCLUDED.fetch_status
            """
        )

        con.execute(
            """
            INSERT INTO etl_batch (fingerprint, rows_seen, rows_accepted, rows_rejected)
            VALUES (%s, %s, %s, %s)
            """,
            (fingerprint, quality['source_rows'], quality['accepted_rows'], quality['rejected_rows']),
        )

        con.execute((ROOT / 'sql/views.sql').read_text(encoding='utf-8'))
        con.commit()
        return {'status': 'loaded', 'new_rows': new_satellites, 'updated_rows': updated_satellites}
    except Exception:
        con.rollback()
        raise

def run(backend='postgres', source_dir=None, refresh=False, max_reject_rate=0.0, database=None):
    if backend != 'postgres':
        raise ValueError("Only PostgreSQL 17 is supported in this repository.")
    if not 0 <= max_reject_rate <= 1:
        raise ValueError('max_reject_rate must be between zero and one')

    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
    output = ROOT / 'reports' / run_id
    output.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(output / 'pipeline.log'), logging.StreamHandler()],
        force=True,
    )
    status = {'run_id': run_id, 'backend': backend, 'started_at': datetime.now(timezone.utc).isoformat()}

    try:
        source_path = Path(source_dir) if source_dir else download(refresh)
        records_by_group = {}
        for group in GROUPS:
            group_file = source_path / f'{group}.json'
            if group_file.exists():
                try:
                    records_by_group[group] = json.loads(group_file.read_text(encoding='utf-8'))
                except Exception as read_err:
                    logging.warning("Could not parse %s as JSON: %s. Skipping group.", group_file, read_err)

        inputs = {g: digest(source_path / f'{g}.json') for g in records_by_group}
        fingerprint = hashlib.sha256(json.dumps({'inputs': inputs, 'contract': 1}, sort_keys=True).encode()).hexdigest()
        status['input_sha256'] = inputs
        status['source_directory'] = str(source_path.resolve())

        fetch_time = datetime.now(timezone.utc)
        satellites, snapshot, sources, rejected, quality = clean(records_by_group, fetch_time)
        save_json(output / 'quality.json', quality)
        if not rejected.empty:
            rejected.to_csv(output / 'quarantine.csv', index=False)

        reject_rate = quality['rejected_rows'] / max(quality['source_rows'], 1)
        if reject_rate > max_reject_rate:
            raise ValueError(f"Data quality gate failed (reject rate {reject_rate:.4f} > {max_reject_rate}); inspect quarantine.csv. Warehouse unchanged.")

        with warehouse(backend, database) as con:
            status.update(load(con, satellites, snapshot, sources, fingerprint, quality, backend))
            from analysis import analyze
            analyze(con, backend, output)

        status['completed_at'] = datetime.now(timezone.utc).isoformat()
        save_json(output / 'run.json', status)
        save_json(ROOT / 'reports/latest.json', status | {'report_directory': str(output)})
        logging.info("Pipeline completed: %s", status)
        return status
    except Exception as exc:
        status.update(status='failed', error_type=type(exc).__name__, error_message=str(exc))
        save_json(output / 'run.json', status)
        logging.error("Pipeline failed (%s): %s", type(exc).__name__, exc)
        raise

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['postgres'], default='postgres')
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--max-reject-rate', type=float, default=0.0)
    args = parser.parse_args()
    run(**vars(args))

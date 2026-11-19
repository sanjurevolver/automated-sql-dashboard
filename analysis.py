"""Domain analysis and summary export module."""
from pathlib import Path
import pandas as pd
from pipeline import frame, save_json
import numpy as np
from scipy.spatial import cKDTree
from datetime import timedelta
from time import perf_counter
import csv
import logging

SCREENING_CONTRACT = 1
TIERS = ('CRITICAL', 'HIGH', 'MEDIUM')


def bootstrap_ci(counts_by_tier, horizon_hours=24, resamples=10000, seed=42):
    """Snapshot bootstrap of mean pair rates per 24 hours, not pair bootstrap.

    Input rows are completed comparable snapshots, columns follow TIERS.
    One snapshot cannot estimate between-snapshot uncertainty.
    """
    counts = np.asarray(counts_by_tier, dtype=float)
    if counts.ndim != 2 or counts.shape[1] != 3 or not np.isfinite(counts).all() or (counts < 0).any():
        raise ValueError('Expected finite nonnegative snapshot-by-tier counts')
    hours = np.broadcast_to(np.asarray(horizon_hours, dtype=float), (len(counts),))
    if not np.isfinite(hours).all() or (hours <= 0).any() or resamples < 1:
        raise ValueError('Require positive horizons and resamples')
    rates = counts * (24 / hours[:, None])
    result = dict(method='snapshot percentile bootstrap', confidence=.95,
                  snapshots=len(counts), resamples=resamples, seed=seed,
                  units='unique pairs per 24-hour snapshot', tiers={})
    if len(counts) < 2:
        result['status'] = 'insufficient_snapshots'
        for i,tier in enumerate(TIERS):
            result['tiers'][tier] = dict(rate=float(rates[0,i]) if len(counts) else None, lower=None, upper=None)
        return result
    rng = np.random.default_rng(seed)
    # Bounded memory even when snapshot history grows.
    means = np.empty((resamples,3))
    for i in range(resamples):
        means[i] = rates[rng.integers(len(rates),size=len(rates))].mean(axis=0)
    bounds = np.quantile(means,[.025,.975],axis=0)
    result['status'] = 'estimated'
    for i,tier in enumerate(TIERS):
        result['tiers'][tier] = dict(rate=float(rates[:,i].mean()),lower=float(bounds[0,i]),upper=float(bounds[1,i]))
    return result


def load_conjunctions(con, snapshot_sk, csv_path, metrics):
    """One bulk COPY, one set-based insert, and completion marker in one lock.

    This function owns a transaction; callers must supply an idle connection.
    The CSV contains canonical satellite keys, distance, speed, TCA, and tier.
    """
    from psycopg.pq import TransactionStatus
    from psycopg.types.json import Jsonb
    if con.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Conjunction load requires an idle connection')
    total = perf_counter()
    with con.transaction():
        con.execute('SELECT pg_advisory_xact_lock(734821)')
        existing = con.execute('SELECT contract_version FROM screening_batch WHERE snapshot_sk=%s', (snapshot_sk,)).fetchone()
        if existing:
            if existing[0] != SCREENING_CONTRACT:
                raise ValueError('Screening contract changed; explicit migration required')
            return {'status':'skipped','new_rows':0,'snapshot_sk':snapshot_sk}
        con.execute('''CREATE TEMP TABLE s_conjunction (
            object_a_sk BIGINT, object_b_sk BIGINT, min_distance_km DOUBLE PRECISION,
            relative_velocity_kms DOUBLE PRECISION, tca_utc TIMESTAMPTZ, risk_level VARCHAR
        ) ON COMMIT DROP''')
        begin = perf_counter()
        with Path(csv_path).open('rb') as source, con.cursor().copy(
                'COPY s_conjunction FROM STDIN WITH (FORMAT CSV)') as copy:
            while block := source.read(1024*1024):
                copy.write(block)
        copy_seconds = perf_counter()-begin
        count = con.execute('SELECT COUNT(*) FROM s_conjunction').fetchone()[0]
        if count != metrics['row_count']:
            raise ValueError('Conjunction file count does not match completed screening')
        # Replacement only for unfinished snapshots; rollback restores prior rows.
        con.execute('DELETE FROM fact_conjunction WHERE snapshot_sk=%s', (snapshot_sk,))
        begin = perf_counter()
        con.execute('''INSERT INTO fact_conjunction
            (snapshot_sk,object_a_sk,object_b_sk,min_distance_km,relative_velocity_kms,tca_utc,risk_level)
            SELECT %s,object_a_sk,object_b_sk,min_distance_km,relative_velocity_kms,tca_utc,risk_level
            FROM s_conjunction''', (snapshot_sk,))
        insert_seconds = perf_counter()-begin
        result = dict(metrics,status='loaded',new_rows=count,snapshot_sk=snapshot_sk,
                      copy_seconds=copy_seconds,insert_seconds=insert_seconds)
        con.execute('''INSERT INTO screening_batch
            (snapshot_sk,contract_version,horizon_hours,row_count,metrics)
            VALUES (%s,%s,24,%s,%s)''', (snapshot_sk,SCREENING_CONTRACT,count,Jsonb(result)))
    result['load_transaction_seconds'] = perf_counter()-total
    logging.info('Conjunction bulk load: %s',result)
    return result


def screen_snapshot(con, source_checksum, omm_records, output):
    """Recover ingestion-success/screening-failure independently on retry."""
    from transform import propagate_forward, normalize_omm_record, refine_tca
    from sgp4.api import Satrec
    from sgp4.omm import initialize
    with con.transaction():
        snapshot = con.execute('''SELECT snapshot_sk,tle_epoch_max FROM dim_snapshot
            WHERE source_file_checksum=%s ORDER BY snapshot_sk LIMIT 1''',(source_checksum,)).fetchone()
        if snapshot is None:
            raise ValueError('No loaded snapshot matches screening source')
        snapshot_sk,start = snapshot
        existing = con.execute('SELECT contract_version FROM screening_batch WHERE snapshot_sk=%s',(snapshot_sk,)).fetchone()
        if existing:
            if existing[0] != SCREENING_CONTRACT:
                raise ValueError('Screening contract changed; explicit migration required')
            result = dict(status='skipped',new_rows=0,snapshot_sk=snapshot_sk)
            save_json(Path(output)/'screening.json',result)
            return result
        keys = dict(con.execute('''SELECT s.norad_id,s.satellite_sk FROM dim_satellite s
            JOIN fact_orbital_position f USING(satellite_sk) WHERE f.snapshot_sk=%s''',(snapshot_sk,)).fetchall())
    if set(keys) != {int(r['NORAD_CAT_ID']) for r in omm_records}:
        raise ValueError('Snapshot state vectors do not match the screening catalog')
    begin = perf_counter()
    positions,velocities = propagate_forward(omm_records,start,start+timedelta(days=1),60)
    propagation = perf_counter()-begin
    del velocities
    begin = perf_counter()
    candidates = collect_candidates(positions,100)
    screen_seconds = perf_counter()-begin
    del positions
    sats = []
    for record in omm_records:
        sat = Satrec(); initialize(sat,normalize_omm_record(record)); sats.append(sat)
    path = Path(output)/'conjunctions.csv'
    path.parent.mkdir(parents=True,exist_ok=True)
    counts = {tier:0 for tier in TIERS}
    begin = perf_counter()
    with path.open('w',newline='',encoding='utf-8') as target:
        writer = csv.writer(target)
        for n,((a,b),item) in enumerate(candidates.items(),1):
            distance,tca,speed = refine_tca(sats[a],sats[b],start+timedelta(seconds=60*item['step']),30)
            if distance < 100:
                tier = 'CRITICAL' if distance < 10 else 'HIGH' if distance < 50 else 'MEDIUM'
                ka,kb = sorted((keys[int(omm_records[a]['NORAD_CAT_ID'])],keys[int(omm_records[b]['NORAD_CAT_ID'])]))
                writer.writerow((ka,kb,distance,speed,tca.isoformat(),tier))
                counts[tier] += 1
            if n % 100000 == 0:
                logging.info('Refined %s/%s candidate pairs',n,len(candidates))
    metrics = dict(row_count=sum(counts.values()),risk_counts=counts,
                   candidate_pairs=len(candidates),propagation_seconds=propagation,
                   screen_seconds=screen_seconds,refinement_and_spool_seconds=perf_counter()-begin)
    result = load_conjunctions(con,snapshot_sk,path,metrics)
    save_json(Path(output)/'screening.json',result)
    return result


def kdtree_screen(positions_at_step, threshold_km=100):
    """Canonical row-index pairs at distance <= threshold; no altitude sieve."""
    points = np.asarray(positions_at_step, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError('Positions must be a finite (objects, 3) array')
    if not np.isfinite(threshold_km) or threshold_km <= 0:
        raise ValueError('threshold_km must be finite and positive')
    return cKDTree(points).query_pairs(threshold_km, output_type='ndarray')


def collect_candidates(positions, threshold_km=100, min_consecutive=1):
    """Best coarse sample and longest consecutive streak per canonical pair.

    Row indices are not warehouse surrogate keys. Sustained filtering is
    opt-in: it can discard fast close approaches. One best sample per pair is
    retained for the benchmark, not a claim that every encounter is refined.
    """
    points = np.asarray(positions)
    if points.ndim != 3 or points.shape[2] != 3:
        raise ValueError('Positions must have shape (objects, steps, 3)')
    if not isinstance(min_consecutive, int) or min_consecutive < 1:
        raise ValueError('min_consecutive must be a positive integer')
    candidates, previous = {}, {}
    for step in range(points.shape[1]):
        pairs = kdtree_screen(points[:,step], threshold_km)
        distances = np.linalg.norm(points[pairs[:,0],step]-points[pairs[:,1],step], axis=1)
        current = {}
        for (a,b), distance in zip(pairs,distances):
            key = (int(a),int(b))
            streak = previous.get(key,0)+1
            current[key] = streak
            item = candidates.get(key)
            if item is None:
                candidates[key] = dict(distance_km=float(distance),step=step,max_consecutive=streak)
            else:
                item['max_consecutive'] = max(item['max_consecutive'],streak)
                if distance < item['distance_km']:
                    item['distance_km'], item['step'] = float(distance),step
        previous = current
    if min_consecutive > 1:
        return {key:value for key,value in candidates.items() if value['max_consecutive'] >= min_consecutive}
    return candidates

def analyze(con, backend, output):
    """Generate summary data and catalog exports for the current pipeline run."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)

    # In Phase 1, export catalog and snapshot dimensions
    satellites = frame(con, 'SELECT * FROM dim_satellite ORDER BY norad_id', backend)
    type_summary = frame(con, 'SELECT * FROM v_satellite_type_summary', backend)
    snapshot_summary = frame(con, 'SELECT * FROM v_snapshot_summary', backend)
    shells = frame(con, 'SELECT * FROM v_orbit_shell_bands ORDER BY shell_sk', backend)

    for name, df in [
        ('dim_satellite', satellites),
        ('v_satellite_type_summary', type_summary),
        ('v_snapshot_summary', snapshot_summary),
        ('dim_orbit_shell', shells),
    ]:
        df.to_csv(output / f'{name}.csv', index=False)

    summary = {
        'total_satellites': len(satellites),
        'payload_count': int((satellites.object_type == 'payload').sum()) if not satellites.empty else 0,
        'debris_count': int((satellites.object_type == 'debris').sum()) if not satellites.empty else 0,
        'rocket_body_count': int((satellites.object_type == 'rocket_body').sum()) if not satellites.empty else 0,
        'unknown_type_count': int((satellites.object_type == 'unknown').sum()) if not satellites.empty else 0,
    }
    save_json(output / 'kpis.json', summary)
    risk = frame(con, '''SELECT r.*,b.horizon_hours FROM v_conjunction_risk_summary r
        JOIN screening_batch b USING(snapshot_sk) ORDER BY snapshot_sk,risk_level''', backend)
    risk.to_csv(output/'risk_tier_distribution.csv',index=False)
    totals = risk.groupby('snapshot_sk',as_index=False)['conjunction_count'].sum()
    totals.to_csv(output/'conjunctions_per_snapshot.csv',index=False)
    if risk.empty:
        counts, hours = np.empty((0,3)), np.empty(0)
    else:
        pivot = risk.pivot(index='snapshot_sk',columns='risk_level',values='conjunction_count').reindex(columns=TIERS,fill_value=0)
        counts = pivot.to_numpy()
        hours = risk.groupby('snapshot_sk')['horizon_hours'].first().reindex(pivot.index).to_numpy()
    save_json(output / 'statistics.json', {'phase':3,'bootstrap':bootstrap_ci(counts,hours)})

    findings = (
        "# Catalog Snapshot Summary\n\n"
        f"Total tracked objects in snapshot: {summary['total_satellites']:,}\n"
        f"- Payloads: {summary['payload_count']:,}\n"
        f"- Debris: {summary['debris_count']:,}\n"
        f"- Rocket bodies: {summary['rocket_body_count']:,}\n"
        f"- Unknown: {summary['unknown_type_count']:,}\n"
    )
    (output / 'insights.md').write_text(findings, encoding='utf-8')

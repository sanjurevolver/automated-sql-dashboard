"""Domain analysis and summary export module."""
from pathlib import Path
import pandas as pd
from pipeline import frame, save_json
import numpy as np
from scipy.spatial import cKDTree


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
    save_json(output / 'statistics.json', {'phase': 1, 'conjunction_analysis': 'pending_phase_3'})

    findings = (
        "# Catalog Snapshot Summary\n\n"
        f"Total tracked objects in snapshot: {summary['total_satellites']:,}\n"
        f"- Payloads: {summary['payload_count']:,}\n"
        f"- Debris: {summary['debris_count']:,}\n"
        f"- Rocket bodies: {summary['rocket_body_count']:,}\n"
        f"- Unknown: {summary['unknown_type_count']:,}\n"
    )
    (output / 'insights.md').write_text(findings, encoding='utf-8')

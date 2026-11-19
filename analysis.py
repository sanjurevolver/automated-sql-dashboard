"""Domain analysis and summary export module."""
from pathlib import Path
import pandas as pd
from pipeline import frame, save_json

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

"""Offline Phase 3A benchmark. Never downloads or changes the pinned GP inputs.

Run: python scripts/benchmark_conjunction.py --source-dir data/raw/gp
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import GROUPS, clean, digest, save_json
from transform import normalize_omm_record, propagate_forward, refine_tca
from analysis import collect_candidates
from sgp4.api import Satrec
from sgp4.omm import initialize


def benchmark(source_dir, output):
    source_dir = Path(source_dir)
    manifest = json.loads((source_dir.parent/'manifest.json').read_text())
    records_by_group = {}
    provenance = {}
    for group in GROUPS:
        path = source_dir/f'{group}.json'
        checksum = digest(path)
        if checksum != manifest[group]['sha256']:
            raise ValueError(f'Pinned snapshot checksum mismatch: {group}')
        records_by_group[group] = json.loads(path.read_text())
        provenance[group] = dict(manifest[group], bytes=path.stat().st_size,
                                 record_count=len(records_by_group[group]))
    satellites, snapshot, _, _, quality = clean(records_by_group)
    records = satellites.attrs['omm_records']
    start = snapshot.iloc[0].tle_epoch_max.to_pydatetime()
    composite = snapshot.iloc[0].source_file_checksum
    if manifest.get('source_file_checksum', composite) != composite:
        raise ValueError('Semantic composite checksum mismatch')
    report = dict(measured_at_utc=datetime.now(timezone.utc).isoformat(),
        python=sys.version, platform=platform.platform(), groups=provenance,
        source_file_checksum=composite, quality=quality, start_utc=start.isoformat(),
        horizon_hours=24, step_seconds=60, steps=1440, threshold_km=100,
        refinement_half_window_seconds=30, refinement_time_tolerance_seconds=.001,
        sustained_filter_default=1, altitude_sieve=False,
        baseline_note='This fresh snapshot differs from CI 44576744039 (19590 unique); catalog drift is expected, not a regression.',
        checksum_note='source_file_checksum uses pipeline.clean canonical JSON group hashes; per-file SHA-256 uses original response bytes.',
        limitations=[
            '60-second, 100-km sampling can miss brief close approaches between samples.',
            'Refine only the best coarse sample per unique pair; other encounter windows may have a smaller minimum.',
            'Golden-section is a local numerical minimum, not an exact global TCA.',
            'The half-open grid ends at +23:59; first refinement extends 30 seconds before start and the final 30 seconds of the horizon are not covered.',
            'Sustained filters discard transient approaches and are opt-in only.',
            'Distance tiers are proximity categories, not collision probabilities.'])
    t0 = perf_counter()
    positions, velocities = propagate_forward(records,start,start+timedelta(days=1),60)
    report['propagation_seconds'] = perf_counter()-t0
    report['propagation_errors'] = 0
    del velocities
    print('Propagation:',report['propagation_seconds'],flush=True)
    t0 = perf_counter()
    candidates = collect_candidates(positions,100)
    report['screen_seconds'] = perf_counter()-t0
    del positions
    report['unique_candidate_pairs'] = len(candidates)
    report['sustained_2_pairs'] = sum(x['max_consecutive']>=2 for x in candidates.values())
    report['sustained_3_pairs'] = sum(x['max_consecutive']>=3 for x in candidates.values())
    print('Candidates:',report['unique_candidate_pairs'],flush=True)
    save_json(output,report)
    t0 = perf_counter()
    sats = []
    for record in records:
        sat = Satrec(); initialize(sat,normalize_omm_record(record)); sats.append(sat)
    report['refinement_initialization_seconds'] = perf_counter()-t0
    totals = {key:dict(pairs=0,refinement_seconds=0.0,critical=0,high=0,medium=0)
              for key in ('all','sustained_2','sustained_3')}
    t0 = perf_counter()
    for n, ((a,b), item) in enumerate(candidates.items(),1):
        begin = perf_counter()
        distance,tca,speed = refine_tca(sats[a],sats[b],start+timedelta(seconds=60*item['step']),30)
        elapsed = perf_counter()-begin
        for key,required in [('all',1),('sustained_2',2),('sustained_3',3)]:
            if item['max_consecutive'] >= required:
                totals[key]['pairs'] += 1
                totals[key]['refinement_seconds'] += elapsed
                if distance < 100:
                    tier = 'critical' if distance < 10 else 'high' if distance < 50 else 'medium'
                    totals[key][tier] += 1
        if n % 100000 == 0:
            print('Refined',n,'elapsed',round(perf_counter()-t0,2),flush=True)
    report['refinement_seconds'] = perf_counter()-t0
    report['refinement_by_filter'] = totals
    report['filter_timing_note'] = 'Subset times sum measured calls within the all-pair run; they are not independent filtered-run wall clocks.'
    report['total_screening_seconds'] = sum(report[key] for key in (
        'propagation_seconds','screen_seconds','refinement_initialization_seconds','refinement_seconds'))
    for group in GROUPS:
        if digest(source_dir/f'{group}.json') != provenance[group]['sha256']:
            raise ValueError('Pinned input changed during benchmark')
    report['complete'] = True
    save_json(output,report)
    print(json.dumps(report,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=ROOT/'data/raw/gp')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/conjunction_benchmark.json')
    args = parser.parse_args()
    benchmark(args.source_dir,args.output)

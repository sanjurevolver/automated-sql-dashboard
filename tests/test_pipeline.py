"""Unit and integration tests for the satellite constellation tracking pipeline."""
from datetime import datetime, timezone
import os
import pytest
from pipeline import clean, derive_object_type, derive_operator, load, warehouse

def sample_gp_records():
    return {
        'active': [
            {
                'OBJECT_NAME': 'STARLINK-1007',
                'OBJECT_ID': '2019-074A',
                'NORAD_CAT_ID': 44713,
                'EPOCH': '2026-11-19T00:00:00.000000',
                'MEAN_MOTION': 15.06,
                'ECCENTRICITY': 0.0001,
                'INCLINATION': 53.05,
                'BSTAR': 0.00001,
            },
            {
                'OBJECT_NAME': 'ONEWEB-0012',
                'OBJECT_ID': '2020-008B',
                'NORAD_CAT_ID': 45132,
                'EPOCH': '2026-11-19T01:00:00.000000',
                'MEAN_MOTION': 13.1,
                'ECCENTRICITY': 0.0002,
                'INCLINATION': 87.4,
                'BSTAR': 0.00002,
            },
        ],
        'debris': [
            {
                'OBJECT_NAME': 'COSMOS 2251 DEB',
                'OBJECT_ID': '1993-036KW',
                'NORAD_CAT_ID': 34000,
                'EPOCH': '2026-11-19T02:00:00.000000',
                'MEAN_MOTION': 14.5,
                'ECCENTRICITY': 0.02,
                'INCLINATION': 74.0,
                'BSTAR': 0.0001,
            }
        ],
    }

def test_two_stage_object_type_derivation():
    # 1. Active group payload
    otype, conf = derive_object_type('active', 'STARLINK-1234')
    assert otype == 'payload' and conf is None

    # 2. Debris group debris
    otype, conf = derive_object_type('cosmos-2251-debris', 'COSMOS 2251 DEB')
    assert otype == 'debris' and conf is None

    # 3. Rocket body in active group triggers quarantine conflict
    otype, conf = derive_object_type('active', 'CZ-6A R/B')
    assert otype == 'rocket_body' and conf == 'group_name_conflict'

    # 4. Rocket body in debris group is accepted as rocket_body without group conflict
    otype, conf = derive_object_type('debris', 'CZ-6A R/B')
    assert otype == 'rocket_body' and conf is None

    # 5. Bare OBJECT A is classified as unknown
    otype, conf = derive_object_type('active', 'OBJECT A')
    assert otype == 'unknown' and conf is None

    # 6. Debris token in active group triggers quarantine conflict
    otype, conf = derive_object_type('active', 'IRIDIUM 33 DEB')
    assert otype == 'debris' and conf == 'group_name_conflict'

def test_operator_derivation():
    assert derive_operator('STARLINK-1007') == 'SpaceX'
    assert derive_operator('ONEWEB-0123') == 'Eutelsat OneWeb'
    assert derive_operator('FLOCK 4P 1') == 'Planet Labs'
    assert derive_operator('IRIDIUM 100') == 'Iridium Communications'
    assert derive_operator('COSMOS 2251') == 'Roscosmos'
    assert derive_operator('GPS BIIF-1') == 'USSF'
    assert derive_operator('GALILEO 26') == 'ESA'
    assert derive_operator('FENGYUN 1C') == 'CNSA'
    assert derive_operator('ISS (ZARYA)') == 'Multinational'
    assert derive_operator('UNKNOWN OBJECT') == 'unknown'

def test_clean_validation_and_quarantine():
    records = sample_gp_records()
    # Introduce invalid NORAD ID and group conflict
    records['active'].append({
        'OBJECT_NAME': 'BAD NORAD',
        'OBJECT_ID': '2020-001A',
        'NORAD_CAT_ID': 'bad_id',
        'EPOCH': '2026-11-19T00:00:00Z',
    })
    records['active'].append({
        'OBJECT_NAME': 'CZ-6A R/B',
        'OBJECT_ID': '2022-098B',
        'NORAD_CAT_ID': 54216,
        'EPOCH': '2026-11-19T00:00:00Z',
    })

    satellites, snapshot, sources, rejected, quality = clean(records)
    assert len(satellites) == 3
    assert len(rejected) == 2
    assert set(rejected['rejection_reason']) == {'invalid_norad_id;', 'group_name_conflict;'}
    assert quality['accepted_rows'] == 3
    assert quality['rejected_rows'] == 2

def test_conflicting_norad_id_in_batch():
    records = {
        'active': [
            {
                'OBJECT_NAME': 'SAT A',
                'OBJECT_ID': '2020-001A',
                'NORAD_CAT_ID': 10001,
                'EPOCH': '2026-11-19T00:00:00Z',
            },
            {
                'OBJECT_NAME': 'SAT B',  # Conflicting name for same NORAD ID
                'OBJECT_ID': '2020-001B',
                'NORAD_CAT_ID': 10001,
                'EPOCH': '2026-11-19T00:00:00Z',
            },
        ]
    }
    satellites, snapshot, sources, rejected, quality = clean(records)
    assert len(satellites) == 0
    assert len(rejected) == 2
    assert all('conflicting_norad_id' in r for r in rejected['rejection_reason'])

def test_deduplication_across_groups():
    records = {
        'active': [
            {
                'OBJECT_NAME': 'STARLINK-1007',
                'OBJECT_ID': '2019-074A',
                'NORAD_CAT_ID': 44713,
                'EPOCH': '2026-11-19T00:00:00Z',
            }
        ],
        'starlink': [
            {
                'OBJECT_NAME': 'STARLINK-1007',
                'OBJECT_ID': '2019-074A',
                'NORAD_CAT_ID': 44713,
                'EPOCH': '2026-11-19T00:00:00Z',
            }
        ],
    }
    satellites, snapshot, sources, rejected, quality = clean(records)
    assert len(satellites) == 1
    assert len(rejected) == 0

def test_warehouse_loading_and_idempotence():
    if not os.getenv('TEST_DATABASE_URL'):
        pytest.skip('Set TEST_DATABASE_URL to an empty, disposable PostgreSQL database')

    database = os.getenv('TEST_DATABASE_URL')
    records = sample_gp_records()
    satellites, snapshot, sources, rejected, quality = clean(records)

    with warehouse('postgres', database) as con:
        # Initial load
        result = load(con, satellites, snapshot, sources, 'batch_test_1', quality, 'postgres')
        assert result['status'] == 'loaded'
        assert result['new_rows'] == 3

        # Fact table invariant check: fact_orbital_position is empty in Phase 1
        fact_count = con.execute('SELECT COUNT(*) FROM fact_orbital_position').fetchone()[0]
        assert fact_count == 0

        # Idempotence: re-running with same fingerprint skips
        skip_result = load(con, satellites, snapshot, sources, 'batch_test_1', quality, 'postgres')
        assert skip_result['status'] == 'skipped'
        assert skip_result['new_rows'] == 0

        # Updating satellite attribute
        sat_modified = satellites.copy()
        sat_modified.loc[0, 'operator'] = 'SpaceX Updated'
        update_result = load(con, sat_modified, snapshot, sources, 'batch_test_2', quality, 'postgres')
        assert update_result['status'] == 'loaded'
        assert update_result['updated_rows'] == 1

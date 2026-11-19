"""Bulk load atomicity, retries and snapshot-level statistical inference."""
import csv
import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from analysis import bootstrap_ci, load_conjunctions, screen_snapshot


def test_bootstrap_snapshot_units_and_reproducibility():
    result = bootstrap_ci([[0,2,8],[2,4,6]],resamples=2000)
    assert result == bootstrap_ci([[0,2,8],[2,4,6]],resamples=2000)
    assert result['tiers']['CRITICAL'] == dict(rate=1,lower=0,upper=2)
    assert result['tiers']['HIGH'] == dict(rate=3,lower=2,upper=4)
    assert result['tiers']['MEDIUM'] == dict(rate=7,lower=6,upper=8)
    assert bootstrap_ci([[2,4,6]],48)['tiers']['CRITICAL']['rate'] == 1
    assert bootstrap_ci([[2,4,6]])['tiers']['HIGH']['lower'] is None
    assert bootstrap_ci(np.empty((0,3)))['status'] == 'insufficient_snapshots'
    with pytest.raises(ValueError): bootstrap_ci([[-1,2,3]])


@pytest.fixture
def database():
    dsn = os.getenv('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Set TEST_DATABASE_URL to disposable PostgreSQL')
    import psycopg
    from psycopg import sql
    schema = 'screening_' + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(dsn) as con:
        con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        con.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        con.execute((root/'sql/schema.sql').read_text())
        con.execute((root/'sql/views.sql').read_text())
        con.execute("""INSERT INTO dim_satellite(norad_id,object_name,operator,object_type)
            VALUES (1,'A','Operator A','payload'),(2,'B','Operator B','debris');
            INSERT INTO dim_snapshot(snapshot_id,tle_fetch_time,tle_epoch_min,tle_epoch_max,source_file_checksum)
            VALUES ('snapshot',now(),now(),now(),'hash')""")
        con.commit()
        try:
            yield con
        finally:
            con.rollback()
            con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
            con.commit()


def write_rows(tmp_path, rows):
    path = tmp_path/'pairs.csv'
    with path.open('w',newline='') as f:
        csv.writer(f).writerows(rows)
    return path


ROW = (1,2,5,7,'2026-11-20T00:00:00Z','CRITICAL')


def test_bulk_copy_idempotence_and_completed_zero(database,tmp_path):
    result = load_conjunctions(database,1,write_rows(tmp_path,[ROW]),dict(row_count=1))
    assert result['new_rows'] == 1 and result['copy_seconds'] >= 0
    assert result['load_transaction_seconds'] >= result['copy_seconds']
    assert load_conjunctions(database,1,tmp_path/'missing.csv',dict(row_count=1))['status'] == 'skipped'
    with database.transaction():
        assert database.execute('SELECT COUNT(*) FROM fact_conjunction').fetchone()[0] == 1
        assert database.execute('SELECT object_a_operator,object_b_operator FROM v_conjunction_ledger').fetchone() == ('Operator A','Operator B')
        database.execute("""INSERT INTO dim_snapshot(snapshot_id,tle_fetch_time,tle_epoch_min,tle_epoch_max,source_file_checksum)
            VALUES ('empty',now(),now(),now(),'emptyhash')""")
    assert load_conjunctions(database,2,write_rows(tmp_path,[]),dict(row_count=0))['new_rows'] == 0
    with database.transaction():
        assert database.execute('SELECT COUNT(*) FROM screening_batch').fetchone()[0] == 2


def test_bulk_failure_rolls_back_and_retry_recovers(database,tmp_path):
    import psycopg
    invalid = (1,2,50,7,'2026-11-20T00:00:00Z','CRITICAL')
    with pytest.raises(psycopg.errors.CheckViolation):
        load_conjunctions(database,1,write_rows(tmp_path,[invalid]),dict(row_count=1))
    with database.transaction():
        assert database.execute('SELECT COUNT(*) FROM screening_batch').fetchone()[0] == 0
        assert database.execute('SELECT COUNT(*) FROM fact_conjunction').fetchone()[0] == 0
    with pytest.raises(ValueError,match='count'):
        load_conjunctions(database,1,write_rows(tmp_path,[ROW]),dict(row_count=2))
    assert load_conjunctions(database,1,write_rows(tmp_path,[ROW]),dict(row_count=1))['new_rows'] == 1
    # Recovery resolves the original snapshot by checksum and bypasses computation.
    assert screen_snapshot(database,'hash',[],tmp_path)['status'] == 'skipped'

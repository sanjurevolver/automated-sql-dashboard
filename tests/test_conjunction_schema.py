"""PostgreSQL contract tests; use an isolated schema and roll back all changes."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_conjunction_constraints_and_views():
    dsn = os.getenv('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Set TEST_DATABASE_URL to a disposable PostgreSQL database')
    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn) as con:
        try:
            schema = 'test_conjunction_' + uuid4().hex
            con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            con.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            for _ in range(2):
                con.execute((ROOT / 'sql/schema.sql').read_text())
                con.execute((ROOT / 'sql/views.sql').read_text())
            con.execute("""
                INSERT INTO dim_satellite (norad_id, object_name, operator, object_type)
                VALUES (1, 'A', 'Alpha', 'payload'), (2, 'B', 'Beta', 'debris');
                INSERT INTO dim_snapshot
                  (snapshot_id, tle_fetch_time, tle_epoch_min, tle_epoch_max, source_file_checksum)
                VALUES ('snapshot-test', now(), now(), now(), 'test');
            """)
            assert con.execute('SELECT SUM(conjunction_count) FROM v_conjunction_risk_summary').fetchone()[0] == 0
            insert = '''INSERT INTO fact_conjunction
                (snapshot_sk, object_a_sk, object_b_sk, min_distance_km,
                 relative_velocity_kms, tca_utc, risk_level)
                VALUES (1, %s, %s, %s, %s, now(), %s)'''
            for distance, tier in [(0, 'CRITICAL'), (9.999, 'CRITICAL'),
                                   (10, 'HIGH'), (49.999, 'HIGH'),
                                   (50, 'MEDIUM'), (99.999, 'MEDIUM')]:
                con.execute(insert, (1, 2, distance, 7, tier))
                assert con.execute('SELECT object_a_name, object_b_name FROM v_conjunction_ledger').fetchone() == ('A', 'B')
                assert con.execute('SELECT conjunction_count FROM v_conjunction_risk_summary WHERE risk_level = %s', (tier,)).fetchone()[0] == 1
                with pytest.raises(psycopg.errors.UniqueViolation):
                    with con.transaction():
                        con.execute(insert, (1, 2, distance, 7, tier))
                con.execute('DELETE FROM fact_conjunction')
            for a, b, distance, velocity, tier in [
                (2, 1, 5, 7, 'CRITICAL'), (1, 1, 5, 7, 'CRITICAL'),
                (1, 2, -1, 7, 'CRITICAL'), (1, 2, 100, 7, 'MEDIUM'),
                (1, 2, float('nan'), 7, 'MEDIUM'),
                (1, 2, 5, float('inf'), 'CRITICAL'),
                (1, 2, 5, -1, 'CRITICAL'), (1, 2, 10, 7, 'CRITICAL'),
                (1, 2, 50, 7, 'HIGH'), (1, 2, 5, 7, 'UNKNOWN'),
            ]:
                with pytest.raises(psycopg.errors.CheckViolation):
                    with con.transaction():
                        con.execute(insert, (a, b, distance, velocity, tier))
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with con.transaction():
                    con.execute(insert, (1, 999, 5, 7, 'CRITICAL'))
        finally:
            con.rollback()

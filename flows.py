"""Prefect orchestration entrypoint.

PAUSED (INTERIM PHASE): Domain transition from banking to satellite tracking.
Flow execution is paused until Phase 4 when the 6-hour cron schedule (0 */6 * * *)
and SGP4 timeout configuration are finalized.
"""
import logging
import os
from pathlib import Path

os.environ.setdefault('PREFECT_HOME', str(Path(__file__).parent / '.prefect'))
os.environ.setdefault('PREFECT_SERVER_ANALYTICS_ENABLED', 'false')

try:
    from prefect import flow
except ImportError:
    flow = lambda *args, **kwargs: (lambda f: f)


@flow(name='satellite-tracking-pipeline', retries=2, retry_delay_seconds=30, log_prints=True)
def analytics_flow(backend='postgres', refresh=False):
    logging.info(
        "Flow execution is currently paused during domain transition (Phases 1-3). "
        "Scheduled execution will resume in Phase 4."
    )
    return {'status': 'paused', 'phase': 'interim'}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    analytics_flow()

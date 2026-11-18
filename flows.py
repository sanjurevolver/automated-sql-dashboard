"""Run once with python flows.py; register a schedule with python flows.py --serve."""
import os
from pathlib import Path
os.environ.setdefault('PREFECT_HOME',str(Path(__file__).parent/'.prefect'))
os.environ.setdefault('PREFECT_SERVER_ANALYTICS_ENABLED','false')
from prefect import flow
from prefect.schedules import Cron
from pipeline import run

@flow(name='sql-to-dashboard',retries=2,retry_delay_seconds=30,log_prints=True)
def analytics_flow(backend='duckdb',refresh=False):
    return run(backend=backend,refresh=refresh)

if __name__=='__main__':
    import sys
    if '--serve' in sys.argv:
        analytics_flow.serve(name='banking-daily',schedule=Cron('0 7 * * *',timezone='Asia/Kolkata'),
            parameters={'backend':os.getenv('WAREHOUSE_BACKEND','duckdb')})
    else:
        analytics_flow(backend=os.getenv('WAREHOUSE_BACKEND','duckdb'))

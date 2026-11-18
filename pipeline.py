"""Reproducible PKDD'99 ingestion, validation and atomic warehouse loading."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
SOURCE = 'https://raw.githubusercontent.com/jlacko/berka-dataset/77e9972a1ead107e5f42a91dd989d587bff055a4/'
OPERATIONS = {'VYBER': 'Cash withdrawal', 'VKLAD': 'Cash deposit',
              'PREVOD Z UCTU': 'Transfer from another bank',
              'PREVOD NA UCET': 'Transfer to another bank',
              'VYBER KARTOU': 'Card withdrawal', 'UNKNOWN': 'Not supplied'}
TYPES = {'PRIJEM': 'credit', 'VYDAJ': 'debit', 'VYBER': 'debit'}
FACT_COLUMNS = ['trans_id','account_id','date_key','operation','direction','amount',
                'signed_amount','balance','purpose','high_value']

def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding='utf-8')
    temp.replace(path)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def download(refresh=False):
    folder = ROOT / 'data/raw'
    folder.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1,
        status_forcelist=[429,500,502,503,504], allowed_methods=['GET'])))
    metadata = {}
    manifest = folder / 'manifest.json'
    old = json.loads(manifest.read_text()) if manifest.exists() else {}
    for name in ['account.asc', 'trans.asc']:
        path = folder / name
        if path.exists() and not refresh:
            if name not in old or old[name]['sha256'] != digest(path):
                raise ValueError(f'Raw cache checksum mismatch: {name}; use --refresh')
            metadata[name] = old[name]
            continue
        url = SOURCE + name
        temporary = path.with_suffix('.part')
        try:
            with session.get(url, stream=True, timeout=(15,120)) as response:
                response.raise_for_status()
                with temporary.open('wb') as target:
                    for chunk in response.iter_content(1024*1024):
                        target.write(chunk)
                with temporary.open('rb') as check:
                    header = check.read(512)
                if b'account_id' not in header or (name=='trans.asc' and b'trans_id' not in header):
                    raise ValueError(f'Unexpected source schema: {name}')
                metadata[name] = {'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),
                    'sha256':digest(temporary),'bytes':temporary.stat().st_size,
                    'etag':response.headers.get('ETag')}
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    session.close()
    save_json(manifest, metadata)
    return folder

def parse_date(values):
    return pd.to_datetime(values.astype('string'), format='%y%m%d', errors='coerce')

def clean(accounts, transactions):
    """Never impute money or discard financial outliers. Return rejected source rows."""
    a = accounts.copy()
    required_a = {'account_id','district_id','frequency','date'}
    required_t = {'trans_id','account_id','date','type','operation','amount','balance','k_symbol'}
    if not required_a.issubset(a) or not required_t.issubset(transactions):
        raise ValueError('Missing required source columns')
    for col in ['account_id','district_id']:
        a[col] = pd.to_numeric(a[col], errors='coerce')
        if a[col].isna().any() or ((a[col]%1)!=0).any() or (a[col]<=0).any():
            raise ValueError(f'Invalid dimension key: {col}')
        a[col] = a[col].astype('int64')
    a['opened_date'] = parse_date(a['date'])
    if a.opened_date.isna().any() or a.frequency.isna().any():
        raise ValueError('Invalid account attributes')
    a = a[['account_id','district_id','frequency','opened_date']].drop_duplicates()
    if a.account_id.duplicated().any():
        raise ValueError('Conflicting duplicate account IDs')
    t = transactions.copy()
    raw_count = len(t)
    t = t.drop_duplicates()
    exact_duplicates = raw_count-len(t)
    for col in ['trans_id','account_id','amount','balance']:
        t[col] = pd.to_numeric(t[col], errors='coerce')
    t['date_key'] = parse_date(t['date'])
    t['direction'] = t['type'].map(TYPES)
    t['operation'] = t.operation.fillna('UNKNOWN').replace(r'^\s*$', 'UNKNOWN', regex=True)
    t['purpose'] = t.k_symbol.fillna('UNKNOWN').replace(r'^\s*$', 'UNKNOWN', regex=True)
    reasons = pd.Series('', index=t.index)
    def flag(mask, reason):
        reasons.loc[mask.fillna(True)] += reason + ';'
    for col in ['trans_id','account_id']:
        flag(t[col].isna() | (t[col]%1!=0) | (t[col]<=0), 'invalid_'+col)
    flag(t.trans_id.duplicated(keep=False), 'conflicting_transaction_id')
    flag(~t.account_id.isin(a.account_id), 'unknown_account')
    flag(t.date_key.isna(), 'invalid_date')
    opened = t.account_id.map(a.set_index('account_id').opened_date)
    flag(t.date_key < opened, 'before_account_open')
    flag(t.direction.isna(), 'unknown_direction')
    flag(~t.operation.isin(OPERATIONS), 'unknown_operation')
    for col in ['amount','balance']:
        flag(~np.isfinite(t[col]), 'invalid_'+col)
        flag((t[col]*100 - (t[col]*100).round()).abs()>1e-5, 'subcent_'+col)
    flag(t.amount<0, 'negative_amount')
    rejected = t.loc[reasons!=''].copy()
    rejected['rejection_reason'] = reasons[reasons!='']
    t = t.loc[reasons==''].copy()
    t['trans_id'] = t.trans_id.astype('int64')
    t['account_id'] = t.account_id.astype('int64')
    t['signed_amount'] = t.amount.where(t.direction=='credit', -t.amount)
    # Fixed, documented operational threshold, stable across incremental batches.
    t['high_value'] = t.amount >= 100000
    report = {'source_rows':raw_count,'exact_duplicates_removed':exact_duplicates,
        'accepted_rows':len(t),'rejected_rows':len(rejected),
        'missing_operations_retained':int((t.operation=='UNKNOWN').sum()),
        'high_value_rows_retained':int(t.high_value.sum()),
        'negative_balance_rows_retained':int((t.balance<0).sum()),
        'high_value_threshold_czk':100000,
        'rules':'Reject invalid keys, unknown codes, invalid money/date and conflicting IDs; retain outliers.'}
    return a, t[FACT_COLUMNS], rejected, report

@contextmanager
def warehouse(backend='duckdb', database=None):
    if backend == 'postgres':
        import psycopg
        dsn = database or os.environ.get('DATABASE_URL')
        if not dsn:
            raise ValueError('Set DATABASE_URL for PostgreSQL')
        con = psycopg.connect(dsn)
    else:
        import duckdb
        path = database or ROOT / 'data/warehouse.duckdb'
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
    try:
        yield con
    finally:
        con.close()

def frame(con, query, backend):
    if backend == 'duckdb':
        return con.execute(query).fetchdf()
    cur = con.execute(query)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

def stage(con, name, df, backend):
    if backend == 'duckdb':
        con.register('_input', df)
        con.execute(f'CREATE TEMP TABLE {name} AS SELECT * FROM _input')
        con.unregister('_input')
    else:
        # Staging columns inherit warehouse SQL types; COPY is streamed, not row INSERTs.
        target = {'s_account':'dim_account','s_date':'dim_date','s_operation':'dim_operation','s_fact':'fact_transaction'}[name]
        con.execute(f'CREATE TEMP TABLE {name} (LIKE {target}) ON COMMIT DROP')
        with con.cursor().copy(f'COPY {name} FROM STDIN') as copy:
            for row in df.itertuples(index=False, name=None):
                copy.write_row(tuple(v.to_pydatetime() if isinstance(v,pd.Timestamp) else v for v in row))

def load(con, accounts, facts, fingerprint, quality, backend):
    con.execute('BEGIN')
    try:
        if backend=='postgres':
            con.execute('SELECT pg_advisory_xact_lock(734821)')
        con.execute((ROOT/'sql/schema.sql').read_text())
        placeholder = '%s' if backend=='postgres' else '?'
        if con.execute(f'SELECT 1 FROM etl_batch WHERE fingerprint={placeholder}',(fingerprint,)).fetchone():
            con.execute((ROOT/'sql/views.sql').read_text())
            con.commit()
            return {'status':'skipped','new_rows':0,'updated_rows':0}
        if facts.empty:
            raise ValueError('No valid transaction rows')
        bounds = con.execute('SELECT MIN(date_key),MAX(date_key) FROM dim_date').fetchone()
        start = min(facts.date_key.min(),pd.Timestamp(bounds[0])) if bounds[0] else facts.date_key.min()
        end = max(facts.date_key.max(),pd.Timestamp(bounds[1])) if bounds[1] else facts.date_key.max()
        dates = pd.Series(pd.date_range(start,end), name='date_key')
        d = pd.DataFrame({'date_key':dates,'year':dates.dt.year,'month':dates.dt.month,
                          'quarter':dates.dt.quarter,'weekday':dates.dt.day_of_week})
        operations = pd.DataFrame(OPERATIONS.items(), columns=['operation','description'])
        for name, df in [('s_account',accounts),('s_date',d),('s_operation',operations),('s_fact',facts)]:
            stage(con,name,df,backend)
        new_rows = con.execute('SELECT COUNT(*) FROM s_fact s LEFT JOIN fact_transaction f USING(trans_id) WHERE f.trans_id IS NULL').fetchone()[0]
        changes = ' OR '.join(f'f.{c} IS DISTINCT FROM s.{c}' for c in FACT_COLUMNS[1:])
        updated = con.execute(f'SELECT COUNT(*) FROM s_fact s JOIN fact_transaction f USING(trans_id) WHERE {changes}').fetchone()[0]
        for target,source,key,cols in [
            ('dim_account','s_account','account_id',list(accounts.columns)),
            ('dim_date','s_date','date_key',list(d.columns)),
            ('dim_operation','s_operation','operation',list(operations.columns)),
            ('fact_transaction','s_fact','trans_id',FACT_COLUMNS)]:
            updates = ','.join(f'{c}=excluded.{c}' for c in cols if c!=key)
            # Only write changed rows. This also permits correcting old-date transactions.
            changed = ' OR '.join(f'{target}.{c} IS DISTINCT FROM excluded.{c}' for c in cols if c!=key)
            con.execute(f'INSERT INTO {target} SELECT * FROM {source} WHERE true ON CONFLICT ({key}) DO UPDATE SET {updates} WHERE {changed}')
        params = ','.join([placeholder]*4)
        con.execute(f'INSERT INTO etl_batch(fingerprint,rows_seen,rows_accepted,rows_rejected) VALUES ({params})',
            (fingerprint,quality['source_rows'],quality['accepted_rows'],quality['rejected_rows']))
        con.execute((ROOT/'sql/views.sql').read_text())
        for name in ['s_fact','s_account','s_date','s_operation']:
            con.execute(f'DROP TABLE {name}')
        con.commit()
        return {'status':'loaded','new_rows':new_rows,'updated_rows':updated}
    except Exception:
        con.rollback()
        raise

def run(backend='duckdb', source_dir=None, refresh=False, max_reject_rate=0.0, database=None):
    if not 0 <= max_reject_rate <= 1:
        raise ValueError('max_reject_rate must be between zero and one')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]
    output = ROOT/'reports'/run_id
    output.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(output/'pipeline.log'),logging.StreamHandler()], force=True)
    status = {'run_id':run_id,'backend':backend,'started_at':datetime.now(timezone.utc).isoformat()}
    try:
        source = Path(source_dir) if source_dir else download(refresh)
        inputs = {n:digest(source/n) for n in ['account.asc','trans.asc']}
        # Version the transformation contract along with both input files.
        fingerprint = hashlib.sha256(json.dumps({'inputs':inputs,'contract':1},sort_keys=True).encode()).hexdigest()
        status['input_sha256'] = inputs
        status['source_directory'] = str(source.resolve())
        accounts = pd.read_csv(source/'account.asc',sep=';',dtype='string')
        transactions = pd.read_csv(source/'trans.asc',sep=';',dtype='string')
        accounts,facts,rejected,quality = clean(accounts,transactions)
        save_json(output/'quality.json', quality)
        if not rejected.empty:
            rejected.to_csv(output/'quarantine.csv', index=False)
        if quality['rejected_rows']/max(quality['source_rows'],1)>max_reject_rate:
            raise ValueError('Data quality gate failed; inspect quarantine.csv. Warehouse unchanged.')
        with warehouse(backend,database) as con:
            status.update(load(con,accounts,facts,fingerprint,quality,backend))
            from analysis import analyze
            analyze(con,backend,output)
        status['completed_at'] = datetime.now(timezone.utc).isoformat()
        save_json(output/'run.json',status)
        save_json(ROOT/'reports/latest.json',status|{'report_directory':str(output)})
        logging.info('Pipeline completed: %s',status)
        return status
    except Exception as exc:
        status.update(status='failed',error_type=type(exc).__name__)
        save_json(output/'run.json',status)
        logging.error('Pipeline failed (%s); see run artifacts. No success pointer advanced.',type(exc).__name__)
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['duckdb','postgres'],default='duckdb')
    parser.add_argument('--source-dir',type=Path)
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--max-reject-rate',type=float,default=0.0)
    args=parser.parse_args()
    run(**vars(args))

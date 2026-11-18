import os
import pandas as pd
import pytest
from pipeline import clean, load, warehouse

def inputs():
    accounts=pd.DataFrame({'account_id':['1','2'],'district_id':['1','2'],
        'frequency':['POPLATEK MESICNE']*2,'date':['930101']*2})
    transactions=pd.DataFrame({'trans_id':['1','2'],'account_id':['1','2'],'date':['930102','930104'],
        'type':['PRIJEM','VYDAJ'],'operation':['VKLAD','VYBER'],'amount':['200.00','100.00'],
        'balance':['200.00','-10.00'],'k_symbol':[None,'SLUZBY']})
    return accounts,transactions

def test_quality_quarantines_without_imputing_money():
    a,t=inputs()
    t.loc[0,'amount']='bad'
    a,f,reject,q=clean(a,t)
    assert len(f)==1 and len(reject)==1
    assert 'invalid_amount' in reject.iloc[0].rejection_reason
    assert f.iloc[0].signed_amount==-100
    assert q['negative_balance_rows_retained']==1

def test_conflicting_ids_and_exact_duplicates():
    a,t=inputs()
    repeated=pd.concat([t,t.iloc[[0]]],ignore_index=True)
    _,f,_,q=clean(a,repeated)
    assert len(f)==2 and q['exact_duplicates_removed']==1
    repeated.loc[2,'amount']='300'
    _,f,rejected,_=clean(a,repeated)
    assert len(f)==1 and len(rejected)==2

@pytest.mark.parametrize('column,value',[('account_id','999'),('date','invalid'),
    ('date','920101'),('amount','-1'),('balance','inf'),('amount','1.001'),
    ('type','BOGUS'),('operation','BOGUS'),('trans_id','1.5')])
def test_invalid_rows(column,value):
    a,t=inputs()
    t.loc[0,column]=value
    _,f,reject,_=clean(a,t)
    assert len(f)==1 and len(reject)==1

@pytest.mark.parametrize('backend',['duckdb','postgres'])
def test_incremental_corrections_and_rollback(backend):
    if backend=='postgres' and not os.getenv('TEST_DATABASE_URL'):
        pytest.skip('Set TEST_DATABASE_URL to an empty, disposable PostgreSQL database')
    database=os.getenv('TEST_DATABASE_URL') if backend=='postgres' else ':memory:'
    a,t=inputs()
    a,f,_,q=clean(a,t)
    with warehouse(backend,database) as con:
        result=load(con,a,f,'batch1',q,backend)
        assert result['new_rows']==2
        assert load(con,a,f,'batch1',q,backend)['status']=='skipped'
        # An old-date correction and a late-arriving old-date transaction are both retained.
        f.loc[0,'amount']=250
        f.loc[0,'signed_amount']=250
        late=f.iloc[[0]].copy()
        late['trans_id']=3
        f=pd.concat([f,late],ignore_index=True)
        result=load(con,a,f,'batch2',q,backend)
        assert result['new_rows']==1 and result['updated_rows']==1
        assert float(con.execute('SELECT SUM(signed_amount) FROM fact_transaction').fetchone()[0])==400
        # Calendar includes Jan 3 even with no transaction.
        assert con.execute('SELECT COUNT(*) FROM dim_date').fetchone()[0]==3
        if backend=='postgres':
            con.commit()
        invalid=f.copy()
        invalid.loc[0,'account_id']=999
        with pytest.raises(Exception):
            load(con,a,invalid,'bad_batch',q,backend)
        assert con.execute("SELECT COUNT(*) FROM etl_batch WHERE fingerprint='bad_batch'").fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM fact_transaction').fetchone()[0]==3
        assert float(con.execute('SELECT SUM(net_flow) FROM v_monthly_cashflow').fetchone()[0])==400

def test_dimension_conflict_fails():
    a,t=inputs()
    extra=a.iloc[[0]].copy()
    extra['district_id']='99'
    with pytest.raises(ValueError,match='Conflicting'):
        clean(pd.concat([a,extra]),t)

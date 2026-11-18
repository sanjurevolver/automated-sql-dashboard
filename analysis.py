"""Account-level statistical analysis and an offline interactive dashboard."""
import json
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats
from pipeline import frame, save_json

def analyze(con, backend, output):
    monthly = frame(con,'SELECT * FROM v_monthly_cashflow ORDER BY month',backend)
    accounts = frame(con,'SELECT * FROM v_account_performance ORDER BY account_id',backend)
    operations = frame(con,'''SELECT operation,direction,COUNT(*) AS transactions,SUM(amount) AS volume
        FROM fact_transaction GROUP BY 1,2 ORDER BY volume DESC''',backend)
    summary = frame(con,'''SELECT COUNT(*) AS transactions,COUNT(DISTINCT account_id) AS accounts,
        MIN(date_key) AS first_date,MAX(date_key) AS last_date,SUM(amount) AS gross_volume,
        SUM(signed_amount) AS net_flow,SUM(CASE WHEN high_value THEN 1 ELSE 0 END) AS high_value_count
        FROM fact_transaction''',backend).iloc[0].to_dict()
    paired = frame(con,'''SELECT account_id,
        AVG(CASE WHEN direction='credit' THEN amount END) AS credit_mean,
        AVG(CASE WHEN direction='debit' THEN amount END) AS debit_mean
        FROM fact_transaction GROUP BY account_id''',backend).dropna()
    difference = paired.credit_mean.astype(float).to_numpy()-paired.debit_mean.astype(float).to_numpy()
    inference = {'question':'Is typical account mean credit size different from mean debit size?',
        'unit':'account; paired mean credit minus mean debit, CZK','paired_accounts':len(difference),
        'method':'Two-sided Wilcoxon signed-rank test; account-resampled percentile bootstrap of mean difference',
        'assumptions':'Accounts treated as independent. Signed-rank location interpretation assumes symmetric paired differences. Accounts may share owners; no causal interpretation.',
        'limitations':'Historical observational sample, not representative of current banking; repeated transactions summarized per account. No fraud labels or inflation adjustment.'}
    if len(difference)>=10 and np.any(difference!=0):
        test = stats.wilcoxon(difference, alternative='two-sided',method='approx')
        rng = np.random.default_rng(2026)
        boot = [float(rng.choice(difference,size=len(difference),replace=True).mean()) for _ in range(2000)]
        inference.update(statistic=float(test.statistic),p_value=float(test.pvalue),
            mean_difference_czk=float(difference.mean()),median_difference_czk=float(np.median(difference)),
            mean_difference_ci95=[float(x) for x in np.quantile(boot,[.025,.975])])
        if test.pvalue == 0:
            inference['p_value_note']='Numerical underflow: the p-value is below floating-point resolution, not literally zero probability.'
    else:
        inference['status']='Insufficient nonzero pairs for inference'
    description = accounts[['transactions','gross_volume','average_amount','net_flow']].astype(float).describe(percentiles=[.01,.25,.5,.75,.95,.99])
    correlation = accounts[['transactions','gross_volume','average_amount','net_flow']].astype(float).corr(method='spearman')
    for name,df in [('monthly_cashflow',monthly),('account_performance',accounts),('operation_mix',operations),
                    ('account_distributions',description.reset_index()),('spearman_correlation',correlation.reset_index())]:
        df.to_csv(output/f'{name}.csv',index=False)
    # Export dimension/fact tables for Power BI offline import; full population, no sampling.
    for name in ['dim_account','dim_date','dim_operation','fact_transaction']:
        frame(con,f'SELECT * FROM {name}',backend).to_csv(output/f'{name}.csv',index=False)
    save_json(output/'statistics.json',inference)
    save_json(output/'kpis.json',summary)
    peak=monthly.loc[monthly.gross_volume.astype(float).idxmax()]
    findings = f'''# Findings from the loaded dataset

- Period: {summary['first_date']} to {summary['last_date']}.
- {int(summary['transactions']):,} transactions across {int(summary['accounts']):,} active accounts.
- Gross ledger volume: {float(summary['gross_volume']):,.2f} CZK; net ledger flow: {float(summary['net_flow']):,.2f} CZK. Neither metric is revenue or profit.
- Peak gross-volume month: {str(peak['month'])[:7]}, {float(peak['gross_volume']):,.2f} CZK. Compare volume per active account before attributing growth to changing behavior.
- {int(summary['high_value_count']):,} transactions meet the fixed 100,000 CZK review threshold. These are not confirmed anomalies or fraud.
- Investigate peak-month operation mix and staffing demand; separately review negative-balance events, without assuming credit default.

## Statistical interpretation

{json.dumps(inference,indent=2)}

Account Spearman correlations are descriptive; gross volume mechanically depends on transaction count and size. Statistical significance does not establish commercial importance. The bootstrap interval reports the account-level mean paired difference, while Wilcoxon tests a rank-based distributional hypothesis. These are distinct estimands.

## Scope

PKDD'99 historical benchmark. Date coverage is measured from the loaded rows. No present-day risk predictions, causal claims, exchange-rate conversions or individual customer identification. Calendar completeness is enforced for daily rolling windows. Monthly windows count observed months; the source has historical coverage and is not a live feed.
'''
    (output/'insights.md').write_text(findings,encoding='utf-8')
    dashboard(monthly,accounts,operations,summary,output)

def dashboard(monthly,accounts,operations,summary,output):
    fig = make_subplots(rows=2,cols=2,subplot_titles=('Monthly cash flows · CZK','Gross volume & three-month average',
        'Operation volume · CZK','Top 20 accounts · gross volume'))
    for col,label in [('inflow','Credits'),('outflow','Debits'),('net_flow','Net flow')]:
        fig.add_trace(go.Scatter(x=monthly.month,y=monthly[col],name=label,mode='lines'),row=1,col=1)
    for col,label in [('gross_volume','Gross volume'),('rolling_3m_volume','3-month mean')]:
        fig.add_trace(go.Scatter(x=monthly.month,y=monthly[col],name=label,mode='lines'),row=1,col=2)
    fig.add_trace(go.Bar(x=operations.operation+' / '+operations.direction,y=operations.volume,name='Operations'),row=2,col=1)
    top=accounts.sort_values('gross_volume',ascending=False).head(20)
    fig.add_trace(go.Bar(x=top.account_id.astype(str),y=top.gross_volume,name='Accounts',
        customdata=top[['district_id','transactions']],hovertemplate='Account %{x}<br>%{y:,.0f} CZK<br>District %{customdata[0]}<br>%{customdata[1]} transactions<extra></extra>'),row=2,col=2)
    fig.update_layout(template='plotly_dark',height=900,paper_bgcolor='#111c30',plot_bgcolor='#111c30',
        margin=dict(t=70,b=100),legend=dict(orientation='h',y=1.1),font=dict(family='Segoe UI'))
    fig.update_xaxes(rangeselector=dict(buttons=[dict(count=1,label='1y',step='year',stepmode='backward'),
        dict(step='all',label='All')]),row=1,col=1)
    chart=fig.to_html(full_html=False,include_plotlyjs=True)
    cards=''.join(f'<div class="card"><small>{label}</small><strong>{value}</strong></div>' for label,value in [
        ('TRANSACTIONS',f"{int(summary['transactions']):,}"),('ACTIVE ACCOUNTS',f"{int(summary['accounts']):,}"),
        ('GROSS VOLUME · CZK',f"{float(summary['gross_volume'])/1e9:,.2f}B"),
        ('NET FLOW · CZK',f"{float(summary['net_flow'])/1e6:,.2f}M")])
    html=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Banking Operations | SQL-to-Dashboard</title><style>body{{margin:0;background:#091322;color:#e6eefb;font-family:Segoe UI,sans-serif}}main{{max-width:1450px;margin:auto;padding:36px}}h1{{font-size:38px;margin:12px 0}}p{{color:#9eb1cc;line-height:1.7}}.tag{{color:#42dfc2;letter-spacing:3px;font-size:12px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:30px 0}}.card{{padding:24px;background:#15233a;border-radius:12px;border-top:3px solid #42dfc2}}small{{display:block;color:#9eb1cc}}strong{{display:block;font-size:30px;margin-top:12px}}a{{color:#42dfc2}}@media(max-width:800px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}</style>
<main><div class="tag">AUTOMATED SQL-TO-DASHBOARD PIPELINE</div><h1>Banking operations, in focus.</h1>
<p>PKDD’99 historical transactions · {str(summary['first_date'])[:10]} — {str(summary['last_date'])[:10]} · Nominal CZK<br>Offline interactive preview. Hover for details, click legends to filter series, drag to zoom.</p>
<div class="cards">{cards}</div>{chart}<p>Ledger movement is not revenue. The 100,000 CZK flag supports review; it is not a fraud prediction.</p>
<p><a href="insights.md">Analysis findings</a> · <a href="statistics.json">Statistical results</a> · <a href="quality.json">Data quality</a></p></main></html>'''
    (output/'dashboard.html').write_text(html,encoding='utf-8')

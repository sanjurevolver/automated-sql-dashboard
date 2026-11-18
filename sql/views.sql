CREATE OR REPLACE VIEW v_monthly_cashflow AS
WITH monthly AS (
 SELECT CAST(date_trunc('month',date_key) AS DATE) AS month,
 COUNT(*) AS transactions, COUNT(DISTINCT account_id) AS active_accounts,
 SUM(amount) AS gross_volume,
 SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END) AS inflow,
 SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END) AS outflow,
 SUM(signed_amount) AS net_flow,
 SUM(CASE WHEN high_value THEN 1 ELSE 0 END) AS high_value_count
 FROM fact_transaction GROUP BY 1
), previous AS (
 SELECT *, LAG(gross_volume) OVER(ORDER BY month) AS previous_volume,
 AVG(gross_volume) OVER(ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_3m_volume
 FROM monthly
)
SELECT *, 100.0*(gross_volume-previous_volume)/NULLIF(previous_volume,0) AS volume_mom_pct
FROM previous;

CREATE OR REPLACE VIEW v_account_performance AS
WITH totals AS (
 SELECT account_id, COUNT(*) AS transactions, SUM(amount) AS gross_volume,
 SUM(signed_amount) AS net_flow, AVG(amount) AS average_amount,
 SUM(CASE WHEN balance<0 THEN 1 ELSE 0 END) AS negative_balance_events,
 SUM(CASE WHEN high_value THEN 1 ELSE 0 END) AS high_value_count
 FROM fact_transaction GROUP BY account_id
)
SELECT t.*, a.district_id, a.frequency,
 DENSE_RANK() OVER(PARTITION BY a.district_id ORDER BY t.gross_volume DESC) AS district_volume_rank
FROM totals t JOIN dim_account a USING(account_id);

CREATE OR REPLACE VIEW v_transaction_detail AS
SELECT f.*,a.district_id,a.frequency,d.year,d.month,d.quarter,d.weekday,o.description AS operation_description
FROM fact_transaction f JOIN dim_account a USING(account_id)
JOIN dim_date d ON f.date_key=d.date_key JOIN dim_operation o USING(operation);

CREATE OR REPLACE VIEW v_daily_cashflow AS
WITH daily AS (
 SELECT date_key, SUM(signed_amount) AS net_flow, COUNT(*) AS transactions
 FROM fact_transaction GROUP BY date_key
)
SELECT d.date_key,COALESCE(t.net_flow,0) AS net_flow,COALESCE(t.transactions,0) AS transactions,
 AVG(COALESCE(t.transactions,0)) OVER(ORDER BY d.date_key ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_7d_transactions
FROM dim_date d LEFT JOIN daily t USING(date_key);

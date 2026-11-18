-- Contributions are ledger flows, NOT revenue or profit. All values are nominal CZK.
-- Top five accounts per district; district_volume_rank uses ties intentionally.
SELECT * FROM v_account_performance WHERE district_volume_rank <= 5
ORDER BY district_id,district_volume_rank;

-- Cohort activity by account opening month, using distinct accounts, not row counts.
WITH cohorts AS (
 SELECT a.account_id, date_trunc('month',a.opened_date) AS cohort,
 date_trunc('month',f.date_key) AS activity_month
 FROM dim_account a JOIN fact_transaction f USING(account_id)
 GROUP BY 1,2,3
), sizes AS (
 SELECT date_trunc('month',opened_date) AS cohort, COUNT(*) AS cohort_size
 FROM dim_account GROUP BY 1
)
SELECT c.cohort,c.activity_month,COUNT(*) AS active_accounts,s.cohort_size,
 100.0*COUNT(*)/s.cohort_size AS activity_pct
FROM cohorts c JOIN sizes s USING(cohort) GROUP BY 1,2,4 ORDER BY 1,2;

-- Diagnostic only: same-day ordering is unavailable, so trans_id is a deterministic
-- tie-breaker, not proof of posting order. Do not label discrepancies as fraud.
WITH ledger AS (
 SELECT *, LAG(balance) OVER(PARTITION BY account_id ORDER BY date_key,trans_id) AS prior_balance
 FROM fact_transaction
)
SELECT trans_id,account_id,date_key,balance-prior_balance-signed_amount AS balance_residual
FROM ledger WHERE prior_balance IS NOT NULL AND ABS(balance-prior_balance-signed_amount)>0.01;

-- PostgreSQL: inspect actual plans after loading; do not claim speedups without measurements.
EXPLAIN ANALYZE SELECT * FROM fact_transaction
WHERE account_id=1 AND date_key BETWEEN DATE '1997-01-01' AND DATE '1997-12-31';

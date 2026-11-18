CREATE TABLE IF NOT EXISTS dim_account (
 account_id BIGINT PRIMARY KEY, district_id INTEGER NOT NULL,
 frequency VARCHAR NOT NULL, opened_date DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS dim_date (
 date_key DATE PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL,
 quarter INTEGER NOT NULL, weekday INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dim_operation (
 operation VARCHAR PRIMARY KEY, description VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_transaction (
 trans_id BIGINT PRIMARY KEY,
 account_id BIGINT NOT NULL REFERENCES dim_account(account_id),
 date_key DATE NOT NULL REFERENCES dim_date(date_key),
 operation VARCHAR NOT NULL REFERENCES dim_operation(operation),
 direction VARCHAR NOT NULL CHECK (direction IN ('credit','debit')),
 amount DECIMAL(18,2) NOT NULL CHECK (amount >= 0),
 signed_amount DECIMAL(18,2) NOT NULL,
 balance DECIMAL(18,2) NOT NULL,
 purpose VARCHAR NOT NULL,
 high_value BOOLEAN NOT NULL,
 CHECK ((direction='credit' AND signed_amount=amount) OR
        (direction='debit' AND signed_amount=-amount))
);
CREATE TABLE IF NOT EXISTS etl_batch (
 fingerprint VARCHAR PRIMARY KEY, loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 rows_seen BIGINT NOT NULL, rows_accepted BIGINT NOT NULL, rows_rejected BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transaction_date ON fact_transaction(date_key);
CREATE INDEX IF NOT EXISTS ix_transaction_account_date ON fact_transaction(account_id,date_key);

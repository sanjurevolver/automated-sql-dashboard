CREATE TABLE IF NOT EXISTS dim_satellite (
  satellite_sk BIGSERIAL PRIMARY KEY,
  norad_id INTEGER NOT NULL UNIQUE,
  object_name VARCHAR NOT NULL,
  operator VARCHAR NOT NULL,
  launch_designator VARCHAR,
  object_type VARCHAR NOT NULL CHECK (object_type IN ('payload','debris','rocket_body','unknown'))
);

CREATE TABLE IF NOT EXISTS dim_orbit_shell (
  shell_sk SERIAL PRIMARY KEY,
  shell_name VARCHAR NOT NULL UNIQUE CHECK (shell_name IN ('LEO-low','LEO-high','MEO','GEO','HEO')),
  altitude_band_low_km DOUBLE PRECISION NOT NULL,
  altitude_band_high_km DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_snapshot (
  snapshot_sk BIGSERIAL PRIMARY KEY,
  snapshot_id VARCHAR NOT NULL UNIQUE,
  tle_fetch_time TIMESTAMPTZ NOT NULL,
  tle_epoch_min TIMESTAMPTZ NOT NULL,
  tle_epoch_max TIMESTAMPTZ NOT NULL,
  source_file_checksum VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_snapshot_source (
  snapshot_sk BIGINT NOT NULL REFERENCES dim_snapshot(snapshot_sk) ON DELETE CASCADE,
  group_name VARCHAR NOT NULL,
  file_checksum VARCHAR NOT NULL,
  record_count INTEGER NOT NULL DEFAULT 0,
  fetch_status VARCHAR NOT NULL DEFAULT 'success',
  PRIMARY KEY (snapshot_sk, group_name)
);

CREATE TABLE IF NOT EXISTS dim_time_epoch (
  epoch_sk BIGSERIAL PRIMARY KEY,
  epoch_utc TIMESTAMPTZ NOT NULL UNIQUE,
  year INTEGER NOT NULL,
  month INTEGER NOT NULL,
  day INTEGER NOT NULL,
  hour INTEGER NOT NULL,
  minute INTEGER NOT NULL,
  second INTEGER NOT NULL,
  unix_epoch_s BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_orbital_position (
  fact_id BIGSERIAL PRIMARY KEY,
  satellite_sk BIGINT NOT NULL REFERENCES dim_satellite(satellite_sk),
  snapshot_sk BIGINT NOT NULL REFERENCES dim_snapshot(snapshot_sk),
  epoch_sk BIGINT NOT NULL REFERENCES dim_time_epoch(epoch_sk),
  position_x DOUBLE PRECISION NOT NULL,
  position_y DOUBLE PRECISION NOT NULL,
  position_z DOUBLE PRECISION NOT NULL,
  velocity_x DOUBLE PRECISION NOT NULL,
  velocity_y DOUBLE PRECISION NOT NULL,
  velocity_z DOUBLE PRECISION NOT NULL,
  altitude_km DOUBLE PRECISION NOT NULL,
  inclination_deg DOUBLE PRECISION NOT NULL,
  eccentricity DOUBLE PRECISION NOT NULL,
  bstar_drag DOUBLE PRECISION NOT NULL,
  lat_deg DOUBLE PRECISION,
  lon_deg DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS etl_batch (
  fingerprint VARCHAR PRIMARY KEY,
  loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  rows_seen BIGINT NOT NULL,
  rows_accepted BIGINT NOT NULL,
  rows_rejected BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orbital_position_satellite ON fact_orbital_position(satellite_sk);
CREATE INDEX IF NOT EXISTS ix_orbital_position_snapshot ON fact_orbital_position(snapshot_sk);
CREATE INDEX IF NOT EXISTS ix_orbital_position_epoch ON fact_orbital_position(epoch_sk);

CREATE OR REPLACE VIEW v_satellite_catalog AS
SELECT
  satellite_sk,
  norad_id,
  object_name,
  operator,
  launch_designator,
  object_type
FROM dim_satellite;

CREATE OR REPLACE VIEW v_snapshot_summary AS
SELECT
  s.snapshot_sk,
  s.snapshot_id,
  s.tle_fetch_time,
  s.tle_epoch_min,
  s.tle_epoch_max,
  s.source_file_checksum,
  COUNT(src.group_name) AS total_groups,
  COALESCE(SUM(src.record_count), 0) AS total_records
FROM dim_snapshot s
LEFT JOIN dim_snapshot_source src USING (snapshot_sk)
GROUP BY
  s.snapshot_sk,
  s.snapshot_id,
  s.tle_fetch_time,
  s.tle_epoch_min,
  s.tle_epoch_max,
  s.source_file_checksum;

CREATE OR REPLACE VIEW v_orbit_shell_bands AS
SELECT
  shell_sk,
  shell_name,
  altitude_band_low_km,
  altitude_band_high_km
FROM dim_orbit_shell;

CREATE OR REPLACE VIEW v_satellite_type_summary AS
SELECT
  object_type,
  COUNT(*) AS object_count,
  COUNT(DISTINCT operator) AS operator_count
FROM dim_satellite
GROUP BY object_type;

CREATE OR REPLACE VIEW v_latest_orbital_positions AS
SELECT
  f.fact_id,
  s.norad_id,
  s.object_name,
  s.operator,
  s.object_type,
  e.epoch_utc,
  f.position_x,
  f.position_y,
  f.position_z,
  f.velocity_x,
  f.velocity_y,
  f.velocity_z,
  f.altitude_km,
  f.inclination_deg,
  f.eccentricity,
  f.bstar_drag,
  f.lat_deg,
  f.lon_deg,
  snap.snapshot_id
FROM fact_orbital_position f
JOIN dim_satellite s USING (satellite_sk)
JOIN dim_snapshot snap USING (snapshot_sk)
JOIN dim_time_epoch e USING (epoch_sk);

CREATE OR REPLACE VIEW v_conjunction_ledger AS
SELECT
  c.conjunction_sk, c.snapshot_sk, snap.snapshot_id,
  c.object_a_sk, a.norad_id AS object_a_norad_id,
  a.object_name AS object_a_name, a.operator AS object_a_operator,
  a.object_type AS object_a_type,
  c.object_b_sk, b.norad_id AS object_b_norad_id,
  b.object_name AS object_b_name, b.operator AS object_b_operator,
  b.object_type AS object_b_type,
  c.min_distance_km, c.relative_velocity_kms, c.tca_utc, c.risk_level
FROM fact_conjunction c
JOIN dim_snapshot snap ON snap.snapshot_sk = c.snapshot_sk
JOIN dim_satellite a ON a.satellite_sk = c.object_a_sk
JOIN dim_satellite b ON b.satellite_sk = c.object_b_sk;

-- Include zero-count tiers for each snapshot; Phase 3B will distinguish
-- completed screening from snapshots that have not yet been screened.
CREATE OR REPLACE VIEW v_conjunction_risk_summary AS
SELECT s.snapshot_sk, s.snapshot_id, tiers.risk_level,
       COUNT(c.conjunction_sk) AS conjunction_count
FROM dim_snapshot s
CROSS JOIN (VALUES ('CRITICAL'), ('HIGH'), ('MEDIUM')) AS tiers(risk_level)
LEFT JOIN fact_conjunction c
  ON c.snapshot_sk = s.snapshot_sk AND c.risk_level = tiers.risk_level
GROUP BY s.snapshot_sk, s.snapshot_id, tiers.risk_level;

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

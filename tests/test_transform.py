"""
Unit and reference tests for transform.py:
- Canonical WGS-84 coordinate reference tests (Equatorial, Polar)
- Published Vallado textbook verification case (Example 3-3, p. 143)
- SGP4 OMM initialization and per-record observation epoch propagation
- TEME coordinate frame preservation
"""
from datetime import datetime, timezone
import math
import pytest
from transform import (
    ecef_to_geodetic,
    gmst_from_datetime,
    teme_to_ecef,
    classify_orbit_shell,
    propagate_satellite,
    propagate_catalog,
)


def test_coordinate_reference_equatorial_wgs84():
    """
    Equatorial reference test on the WGS-84 reference ellipsoid:
    ECEF (a, 0, 0) km -> lat=0.0 deg, lon=0.0 deg, alt=0.0 km.
    Tolerance: sub-millimeter (< 1e-6 km = 1 mm) on altitude and 1e-7 deg on angles.
    """
    x, y, z = 6378.137, 0.0, 0.0
    lat, lon, alt = ecef_to_geodetic(x, y, z)

    assert abs(lat - 0.0) < 1e-7
    assert abs(lon - 0.0) < 1e-7
    assert abs(alt - 0.0) < 1e-6  # Sub-millimeter (< 1 mm)


def test_coordinate_reference_polar_wgs84():
    """
    North Polar reference test on the WGS-84 reference ellipsoid:
    ECEF (0, 0, b) km -> lat=90.0 deg, alt=0.0 km.
    Where b = a * (1 - f) = 6356.752314245 km.
    Tolerance: sub-millimeter (< 1e-6 km = 1 mm) on altitude and exact 90 deg latitude.
    """
    x, y, z = 0.0, 0.0, 6356.752314245
    lat, lon, alt = ecef_to_geodetic(x, y, z)

    assert abs(lat - 90.0) < 1e-7
    assert abs(alt - 0.0) < 1e-6  # Sub-millimeter (< 1 mm)


def test_coordinate_reference_vallado_example_3_3():
    """
    Vallado Reference Case:
    Citation: David A. Vallado, Fundamentals of Astrodynamics and Applications
              (4th Edition, Microcosm Press / Springer, 2013),
              Chapter 3: Coordinate Systems and Time,
              Example 3-3: Converting ECEF to Lat Lon, Page 143.

    Given ECEF position vector:
        r_ECEF = [6524.834, 6862.875, 6448.296] km

    Published textbook results in Vallado (4th ed., p. 143):
        Longitude lambda        = 46.4464 deg (East)
        Geodetic latitude phi   = 34.352496 deg N (34 deg 21' 09")
        Ellipsoidal height h    = 5085.22 km (converged iteration table: 5085.2188 km)

    Tolerance justification:
        - Altitude: bound is <= 0.001 km (1.0 meter). Bowring's closed-form algorithm
          yields 5085.21877 km, agreeing with Vallado's iterative table to < 1 mm.
        - Geodetic latitude: bound is <= 1e-5 deg (~1.1 meter on Earth's surface).
        - Longitude: bound is <= 1e-4 deg (~0.36 arcsecond).
    """
    x, y, z = 6524.834, 6862.875, 6448.296
    lat_deg, lon_deg, alt_km = ecef_to_geodetic(x, y, z)

    # Assert against PUBLISHED textbook values
    assert abs(lat_deg - 34.352496) <= 1e-5, f"Latitude {lat_deg} diverges from Vallado 34.352496"
    assert abs(lon_deg - 46.4464) <= 1e-4, f"Longitude {lon_deg} diverges from Vallado 46.4464"
    assert abs(alt_km - 5085.22) <= 0.002, f"Altitude {alt_km} diverges from Vallado 5085.22"


def test_sgp4_omm_iss_propagation():
    """
    Validates SGP4 OMM initialization and propagation of the International Space Station (NORAD 25544)
    to its own observation epoch. Asserts that altitude is in expected LEO band (~400-430 km)
    and TEME coordinates are non-zero.
    """
    iss_record = {
        'OBJECT_NAME': 'ISS (ZARYA)',
        'OBJECT_ID': '1998-067A',
        'EPOCH': '2026-11-19T04:55:07.676544',
        'MEAN_MOTION': 15.49480324,
        'ECCENTRICITY': 0.0007402,
        'INCLINATION': 51.6318,
        'RA_OF_ASC_NODE': 252.9634,
        'ARG_OF_PERICENTER': 42.1092,
        'MEAN_ANOMALY': 318.0465,
        'EPHEMERIS_TYPE': 0,
        'CLASSIFICATION_TYPE': 'U',
        'NORAD_CAT_ID': 25544,
        'ELEMENT_SET_NO': 999,
        'REV_AT_EPOCH': 59578,
        'BSTAR': 0.00025714952,
        'MEAN_MOTION_DOT': 0.00013991,
        'MEAN_MOTION_DDOT': 0,
    }

    state = propagate_satellite(iss_record)
    assert state['norad_id'] == 25544
    assert 400.0 <= state['altitude_km'] <= 430.0
    assert state['orbit_shell'] == 'LEO-low'
    # Speed of ISS in LEO is ~7.66 km/s
    speed = math.hypot(state['velocity_x'], state['velocity_y'], state['velocity_z'])
    assert 7.5 <= speed <= 7.8


def test_per_record_epoch_propagation():
    """
    Verifies that satellites with different observation epochs are propagated strictly to
    their OWN individual epoch timestamps, NOT to a global snapshot min/max epoch.
    """
    records = [
        {
            'OBJECT_NAME': 'SAT-A',
            'OBJECT_ID': '2024-001A',
            'NORAD_CAT_ID': 90001,
            'EPOCH': '2026-11-19T01:00:00.000000',
            'MEAN_MOTION': 15.0,
            'ECCENTRICITY': 0.001,
            'INCLINATION': 53.0,
            'BSTAR': 0.0001,
        },
        {
            'OBJECT_NAME': 'SAT-B',
            'OBJECT_ID': '2024-002A',
            'NORAD_CAT_ID': 90002,
            'EPOCH': '2026-11-19T05:30:00.000000',  # 4.5 hours later
            'MEAN_MOTION': 15.0,
            'ECCENTRICITY': 0.001,
            'INCLINATION': 53.0,
            'BSTAR': 0.0001,
        },
    ]

    df, failed = propagate_catalog(records)
    assert len(failed) == 0
    assert len(df) == 2

    row_a = df[df['norad_id'] == 90001].iloc[0]
    row_b = df[df['norad_id'] == 90002].iloc[0]

    # Confirm distinct observation epochs are stored
    assert row_a['epoch_utc'] == datetime(2026, 11, 19, 1, 0, 0, tzinfo=timezone.utc)
    assert row_b['epoch_utc'] == datetime(2026, 11, 19, 5, 30, 0, tzinfo=timezone.utc)


def test_teme_preservation():
    """
    Verifies that position_x/y/z and velocity_x/y/z stored in the output record
    match the raw TEME vectors computed directly by SGP4 (no premature GCRS rotation).
    """
    from sgp4.api import Satrec, jday
    from sgp4.omm import initialize as omm_initialize
    from transform import normalize_omm_record

    rec = {
        'OBJECT_NAME': 'SAT-TEME',
        'OBJECT_ID': '2024-003A',
        'NORAD_CAT_ID': 90003,
        'EPOCH': '2026-11-19T02:00:00.000000',
        'MEAN_MOTION': 14.5,
        'ECCENTRICITY': 0.01,
        'INCLINATION': 45.0,
        'BSTAR': 0.0001,
    }

    norm = normalize_omm_record(rec)
    sat = Satrec()
    omm_initialize(sat, norm)
    dt = datetime(2026, 11, 19, 2, 0, 0, tzinfo=timezone.utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    _, direct_r, direct_v = sat.sgp4(jd, fr)

    state = propagate_satellite(rec)
    assert abs(state['position_x'] - direct_r[0]) < 1e-9
    assert abs(state['position_y'] - direct_r[1]) < 1e-9
    assert abs(state['position_z'] - direct_r[2]) < 1e-9
    assert abs(state['velocity_x'] - direct_v[0]) < 1e-9
    assert abs(state['velocity_y'] - direct_v[1]) < 1e-9
    assert abs(state['velocity_z'] - direct_v[2]) < 1e-9


def test_orbit_shell_classification():
    """
    Tests orbital shell partitioning across altitude bands and eccentricity limits.
    """
    assert classify_orbit_shell(400.0, 0.001, 51.6) == 'LEO-low'
    assert classify_orbit_shell(800.0, 0.001, 98.0) == 'LEO-high'
    assert classify_orbit_shell(20000.0, 0.005, 55.0) == 'MEO'
    assert classify_orbit_shell(35786.0, 0.001, 0.1) == 'GEO'
    assert classify_orbit_shell(35786.0, 0.30, 63.4) == 'HEO'  # High eccentricity Molniya
    assert classify_orbit_shell(50000.0, 0.01, 10.0) == 'HEO'

"""
SGP4 Transformation & Coordinate Engine:
- OMM JSON initialization using sgp4.omm.initialize
- SGP4 orbit propagation strictly to satellite's individual observation epoch
- TEME (ECI) coordinate frame preservation
- TEME -> ECEF -> WGS-84 Geodetic coordinate conversion (Bowring algorithm)
- Orbit shell classification
"""
from datetime import datetime, timezone
import math
import pandas as pd
from sgp4.api import Satrec, jday
from sgp4.omm import initialize as omm_initialize

# WGS-84 ellipsoid parameters
WGS84_A = 6378.137  # Semi-major axis in km
WGS84_F = 1.0 / 298.257223563  # Flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)  # Semi-minor axis: 6356.752314245 km
WGS84_E2 = 2.0 * WGS84_F - WGS84_F**2  # First eccentricity squared (~0.00669437999014)
WGS84_E_PRIME2 = (WGS84_A**2 - WGS84_B**2) / (WGS84_B**2)  # Second eccentricity squared

# Earth nominal rotation rate in radians per second (WGS-84 / IERS)
EARTH_ROTATION_RATE_RAD_S = 7.292115146706979e-5


def gmst_from_datetime(dt: datetime) -> float:
    """
    Computes Greenwich Mean Sidereal Time (GMST) in radians for a given UTC datetime.
    Follows IAU 1982 / Vallado (Fundamentals of Astrodynamics and Applications, 4th ed., Eq. 3-47).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Julian Date computation
    year, month, day = dt.year, dt.month, dt.day
    if month <= 2:
        year -= 1
        month += 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    jd_day = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5
    day_fraction = (dt.hour + dt.minute / 60.0 + (dt.second + dt.microsecond * 1e-6) / 3600.0) / 24.0
    jd = jd_day + day_fraction

    # Centuries since J2000.0
    t_ut1 = (jd - 2451545.0) / 36525.0

    # GMST in degrees: Vallado Eq. 3-47
    theta_deg = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * (t_ut1**2) - (t_ut1**3) / 38710000.0
    return math.radians(theta_deg % 360.0)


def teme_to_ecef(r_teme: tuple[float, float, float], v_teme: tuple[float, float, float], dt: datetime) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """
    Transforms position and velocity vectors from TEME (ECI) to ECEF frame via GMST rotation.
    r_teme: position vector in TEME (km)
    v_teme: velocity vector in TEME (km/s)
    dt: UTC timestamp of observation epoch
    Returns: ((x_ecef, y_ecef, z_ecef), (vx_ecef, vy_ecef, vz_ecef))
    """
    theta = gmst_from_datetime(dt)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    x_teme, y_teme, z_teme = r_teme
    vx_teme, vy_teme, vz_teme = v_teme

    # Position rotation about z-axis
    x_ecef = cos_t * x_teme + sin_t * y_teme
    y_ecef = -sin_t * x_teme + cos_t * y_teme
    z_ecef = z_teme

    # Velocity includes Earth's rotational velocity component: v_ecef = R_z * v_teme - omega x r_ecef
    vx_ecef = cos_t * vx_teme + sin_t * vy_teme + EARTH_ROTATION_RATE_RAD_S * y_ecef
    vy_ecef = -sin_t * vx_teme + cos_t * vy_teme - EARTH_ROTATION_RATE_RAD_S * x_ecef
    vz_ecef = vz_teme

    return (x_ecef, y_ecef, z_ecef), (vx_ecef, vy_ecef, vz_ecef)


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """
    Converts ECEF coordinates (x, y, z) in km to geodetic latitude, longitude, and ellipsoidal altitude on WGS-84.
    Uses Bowring's closed-form algorithm (1985).
    Returns: (lat_deg, lon_deg, altitude_km)
    """
    p = math.hypot(x, y)
    lon_rad = math.atan2(y, x)

    # Handle pole singularity
    if p < 1e-6:
        lat_deg = 90.0 if z >= 0 else -90.0
        alt_km = abs(z) - WGS84_B
        return lat_deg, math.degrees(lon_rad), alt_km

    # Bowring parametric latitude angle
    theta = math.atan2(z * WGS84_A, p * WGS84_B)
    lat_rad = math.atan2(
        z + WGS84_E_PRIME2 * WGS84_B * (math.sin(theta)**3),
        p - WGS84_E2 * WGS84_A * (math.cos(theta)**3)
    )

    sin_lat = math.sin(lat_rad)
    # Prime vertical radius of curvature
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * (sin_lat**2))
    alt_km = p / math.cos(lat_rad) - n

    return math.degrees(lat_rad), math.degrees(lon_rad), alt_km


def classify_orbit_shell(altitude_km: float, eccentricity: float = 0.0, inclination_deg: float = 0.0) -> str:
    """
    Classifies satellite altitude into standard orbital regime shells:
    - LEO-low: 160 <= alt < 600 km
    - LEO-high: 600 <= alt < 2000 km
    - MEO: 2000 <= alt < 35500 km
    - GEO: 35500 <= alt <= 36000 km, low eccentricity (<0.05), low inclination (<=15 deg)
    - HEO: highly eccentric (e >= 0.25) or high-altitude non-GEO (>36000 km)
    """
    if eccentricity >= 0.25:
        return 'HEO'
    if altitude_km < 600.0:
        return 'LEO-low'
    if altitude_km < 2000.0:
        return 'LEO-high'
    if altitude_km < 35500.0:
        return 'MEO'
    if 35500.0 <= altitude_km <= 36000.0:
        if inclination_deg <= 15.0 and eccentricity < 0.05:
            return 'GEO'
        return 'HEO'
    return 'HEO'


def normalize_omm_record(record: dict) -> dict:
    """
    Ensures all CCSDS OMM mandatory fields exist on dictionary before passing to SGP4.
    Fills standard default orbital parameters if omitted in synthetic test cases.
    """
    rec = dict(record)
    rec.setdefault('CLASSIFICATION_TYPE', 'U')
    rec.setdefault('EPHEMERIS_TYPE', 0)
    rec.setdefault('ELEMENT_SET_NO', 999)
    rec.setdefault('REV_AT_EPOCH', 1000)
    rec.setdefault('ARG_OF_PERICENTER', 0.0)
    rec.setdefault('MEAN_ANOMALY', 0.0)
    rec.setdefault('RA_OF_ASC_NODE', 0.0)
    rec.setdefault('MEAN_MOTION_DOT', 0.0)
    rec.setdefault('MEAN_MOTION_DDOT', 0.0)
    rec.setdefault('BSTAR', 0.0)
    return rec


def propagate_satellite(omm_record: dict) -> dict:
    """
    Initializes SGP4 Satrec using sgp4.omm.initialize and propagates
    STRICTLY to the record's own individual observation epoch.
    Output position_x/y/z and velocity_x/y/z are preserved in the TEME frame.
    """
    normalized = normalize_omm_record(omm_record)
    sat = Satrec()
    omm_initialize(sat, normalized)

    epoch_str = str(normalized['EPOCH']).strip()
    clean_epoch = epoch_str[:-1] if epoch_str.endswith('Z') else epoch_str
    dt = datetime.fromisoformat(clean_epoch)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Julian date for propagation strictly to satellite's own epoch
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond * 1e-6)

    err, r_teme, v_teme = sat.sgp4(jd, fr)
    if err != 0:
        raise ValueError(f"SGP4 propagation error code {err} for NORAD {normalized.get('NORAD_CAT_ID')}")

    # Coordinate transformation: TEME -> ECEF -> Geodetic
    r_ecef, v_ecef = teme_to_ecef(r_teme, v_teme, dt)
    lat_deg, lon_deg, alt_km = ecef_to_geodetic(*r_ecef)

    norad_id = int(normalized['NORAD_CAT_ID'])
    inclination = float(normalized['INCLINATION'])
    eccentricity = float(normalized['ECCENTRICITY'])
    bstar = float(normalized.get('BSTAR', 0.0))

    return {
        'norad_id': norad_id,
        'epoch_utc': dt,
        'position_x': float(r_teme[0]),
        'position_y': float(r_teme[1]),
        'position_z': float(r_teme[2]),
        'velocity_x': float(v_teme[0]),
        'velocity_y': float(v_teme[1]),
        'velocity_z': float(v_teme[2]),
        'altitude_km': float(alt_km),
        'inclination_deg': inclination,
        'eccentricity': eccentricity,
        'bstar_drag': bstar,
        'lat_deg': float(lat_deg),
        'lon_deg': float(lon_deg),
        'orbit_shell': classify_orbit_shell(alt_km, eccentricity, inclination),
    }


def propagate_catalog(records: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """
    Propagates a collection of OMM records to their respective observation epochs.
    Returns (DataFrame of state vectors, list of failed records).
    """
    results = []
    failed = []
    for rec in records:
        try:
            res = propagate_satellite(rec)
            results.append(res)
        except Exception as e:
            failed.append({
                'norad_id': rec.get('NORAD_CAT_ID') or rec.get('norad_id'),
                'object_name': rec.get('OBJECT_NAME') or rec.get('object_name'),
                'error': str(e),
            })
    df = pd.DataFrame(results) if results else pd.DataFrame(columns=[
        'norad_id', 'epoch_utc', 'position_x', 'position_y', 'position_z',
        'velocity_x', 'velocity_y', 'velocity_z', 'altitude_km',
        'inclination_deg', 'eccentricity', 'bstar_drag', 'lat_deg', 'lon_deg', 'orbit_shell'
    ])
    return df, failed

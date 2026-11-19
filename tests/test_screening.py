"""Analytic encounter and brute-force broadphase checks, without network access."""
from datetime import datetime, timedelta, timezone
import numpy as np
import pytest

from analysis import kdtree_screen, collect_candidates
from transform import propagate_forward, refine_tca

START = datetime(2000, 6, 27, 18, 50, 19, 733568, tzinfo=timezone.utc)
VANGUARD = dict(OBJECT_NAME='VANGUARD 1', OBJECT_ID='1958-002B',
    EPOCH='2000-06-27T18:50:19.733568', NORAD_CAT_ID=5,
    MEAN_MOTION=10.82419157, ECCENTRICITY=.1859667, INCLINATION=34.2682,
    RA_OF_ASC_NODE=348.7242, ARG_OF_PERICENTER=331.7664,
    MEAN_ANOMALY=19.3264, BSTAR=2.8098e-5, MEAN_MOTION_DOT=2.3e-7)


def test_forward_matches_published_vanguard_at_one_day():
    r, v = propagate_forward([VANGUARD], START, START+timedelta(days=2), 86400)
    assert r.shape == v.shape == (1, 2, 3)
    np.testing.assert_allclose(r[0, 1], [-938.55923943,-6268.18748831,-4294.02924751], atol=1e-6, rtol=0)
    np.testing.assert_allclose(v[0, 1], [7.536105209,-.427127707,.989878080], atol=1e-8, rtol=0)
    assert VANGUARD['EPOCH'] == '2000-06-27T18:50:19.733568'


def test_forward_grid_and_invalid_arguments():
    r, v = propagate_forward([], START, START+timedelta(days=1), 60)
    assert r.shape == v.shape == (0,1440,3)
    for start, end, step in [(START,START,60), (START,START+timedelta(seconds=61),60),
                             (START,START+timedelta(days=1),0),
                             (START.replace(tzinfo=None),START+timedelta(days=1),60)]:
        with pytest.raises(ValueError): propagate_forward([],start,end,step)


def test_tree_matches_brute_force_and_boundary():
    p = np.random.default_rng(20).normal(size=(70,3))*100
    expected = {(a,b) for a in range(len(p)) for b in range(a+1,len(p))
                if np.linalg.norm(p[a]-p[b]) <= 100}
    assert set(map(tuple,kdtree_screen(p,100))) == expected
    assert kdtree_screen(np.array([[0,0,0],[100,0,0]]),100).tolist() == [[0,1]]
    assert kdtree_screen(np.empty((0,3)),100).shape == (0,2)
    with pytest.raises(ValueError): kdtree_screen([[float('nan'),0,0]],100)
    with pytest.raises(ValueError): kdtree_screen([[0,0,0]],-1)


def test_sustained_pairs_reset_on_gaps_and_filter_is_explicit():
    p = np.zeros((2,5,3)); p[1,:,0] = [10,9,200,8,7]
    all_pairs = collect_candidates(p,100)
    assert all_pairs[(0,1)]['max_consecutive'] == 2
    assert all_pairs[(0,1)]['step'] == 4
    assert len(collect_candidates(p,100,min_consecutive=2)) == 1
    assert collect_candidates(p,100,min_consecutive=3) == {}


class LinearSatellite:
    """Analytic TEME surrogate with a known closest approach at +7 seconds."""
    def __init__(self, moving=False, error=0):
        from sgp4.api import jday
        self.jd,self.fr = jday(START.year,START.month,START.day,START.hour,START.minute,START.second+START.microsecond/1e6)
        self.moving,self.error = moving,error

    def sgp4(self,jd,fr):
        t = ((jd-self.jd)+(fr-self.fr))*86400
        return self.error, ((t-7,3,0) if self.moving else (0,0,0)), ((1,0,0) if self.moving else (0,0,0))


def test_refinement_known_tca_velocity_and_boundary():
    a,b = LinearSatellite(),LinearSatellite(True)
    distance,tca,speed = refine_tca(a,b,START,30)
    assert distance == pytest.approx(3,abs=1e-6)
    assert (tca-START).total_seconds() == pytest.approx(7,abs=.001)
    assert speed == pytest.approx(1)
    distance,tca,_ = refine_tca(a,b,START,2)
    assert (tca-START).total_seconds() == 2
    assert distance == pytest.approx(np.sqrt(34))
    with pytest.raises(ValueError): refine_tca(a,LinearSatellite(error=6),START,30)
    with pytest.raises(ValueError): refine_tca(a,b,START,0)


def test_forward_propagation_errors_fail_explicitly(monkeypatch):
    import transform
    class FailedSatellite:
        def sgp4_array(self,jd,fr):
            return np.full(len(jd),6),np.zeros((len(jd),3)),np.zeros((len(jd),3))
    monkeypatch.setattr(transform,'Satrec',FailedSatellite)
    monkeypatch.setattr(transform,'omm_initialize',lambda *args: None)
    with pytest.raises(ValueError,match='SGP4'):
        propagate_forward([VANGUARD],START,START+timedelta(minutes=1),60)

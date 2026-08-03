import numpy as np
import pandapower as pp

from gridpilot.grid import N1_MARGIN, load_case


def test_base_case_is_clean_and_balanced():
    net = load_case()
    pp.rundcpp(net)
    loading = np.nan_to_num(net.res_line.loading_percent.values)
    assert loading.max() <= 100.0
    assert net.load.p_mw.sum() > 4000


def test_limits_are_n1_secure():
    """Sized for the worst single contingency, so no single line trip should
    overload anything. This is the property the whole benchmark rests on."""
    net = load_case()
    rng = np.random.default_rng(0)
    for idx in rng.choice(net.line.index.values, size=25, replace=False):
        trial = load_case()
        trial.line.at[idx, "in_service"] = False
        pp.rundcpp(trial, check_connectivity=True)
        loading = np.nan_to_num(trial.res_line.loading_percent.values)
        assert loading.max() <= 100.0 * N1_MARGIN + 1e-6, f"line {idx} broke N-1"


def test_rescaling_limits_does_not_change_physics():
    """Transformer impedance is defined relative to sn_mva, so rescaling
    ratings must not move the power flow."""
    from gridpilot.grid import _bare_case

    bare = _bare_case()
    pp.rundcpp(bare)
    before = np.nan_to_num(bare.res_line.p_from_mw.values)

    net = load_case()
    pp.rundcpp(net)
    after = np.nan_to_num(net.res_line.p_from_mw.values)
    assert np.allclose(before, after, atol=1e-6)


def test_stress_scales_load_and_generation_together():
    a, b = load_case(1.0), load_case(1.2)
    assert np.isclose(b.load.p_mw.sum() / a.load.p_mw.sum(), 1.2)
    assert np.isclose(b.gen.p_mw.sum() / a.gen.p_mw.sum(), 1.2)

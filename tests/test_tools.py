import pytest

from gridpilot.cascade import CascadeSim
from gridpilot.grid import load_case
from gridpilot.tools import PROTECTED_BUSES, ToolError, dispatch, validate, what_if


def test_protected_buses_cannot_be_shed():
    with pytest.raises(ToolError, match="protected"):
        validate({"type": "shed_load", "bus": sorted(PROTECTED_BUSES)[0], "mw": 10})


def test_shed_is_capped_per_action():
    with pytest.raises(ToolError, match="capped"):
        validate({"type": "shed_load", "bus": 3, "mw": 10_000})


def test_malformed_actions_are_rejected_with_a_reason():
    for bad in [
        {"type": "shed_load", "bus": "twelve", "mw": 10},
        {"type": "shed_load", "bus": 3, "mw": -5},
        {"type": "teleport_power", "bus": 3},
        {"type": "open_line"},
        "not even an object",
    ]:
        with pytest.raises(ToolError):
            validate(bad)


def test_what_if_does_not_mutate_the_real_grid():
    sim = CascadeSim(load_case())
    before_served = sim.metrics().load_served_mw
    before_lines = sim.net.line.in_service.sum()
    bus = int(sim.net.load.sort_values("p_mw", ascending=False).iloc[0].bus)
    out = what_if(sim, [{"type": "shed_load", "bus": bus, "mw": 50}])
    assert "would_lose_mw" in out
    assert sim.metrics().load_served_mw == before_served
    assert sim.net.line.in_service.sum() == before_lines


def test_action_budget_per_tick_is_enforced():
    sim = CascadeSim(load_case())
    acts = [{"type": "shed_load", "bus": 3, "mw": 1} for _ in range(9)]
    res = dispatch(sim, "apply_actions", {"actions": acts})
    assert not res.ok
    assert "per tick" in res.detail


def test_partial_failure_still_applies_the_valid_actions():
    sim = CascadeSim(load_case())
    bus = int(sim.net.load.sort_values("p_mw", ascending=False).iloc[0].bus)
    res = dispatch(sim, "apply_actions", {"actions": [
        {"type": "shed_load", "bus": bus, "mw": 20},
        {"type": "shed_load", "bus": sorted(PROTECTED_BUSES)[0], "mw": 20},
    ]})
    assert res.data["applied"] and res.data["rejected"]


def test_grid_state_hides_protected_buses_from_shed_suggestions():
    sim = CascadeSim(load_case())
    state = dispatch(sim, "get_grid_state", {}).data
    assert not (set(state["sheddable_load_by_bus"]) & PROTECTED_BUSES)

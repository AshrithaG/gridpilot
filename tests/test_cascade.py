import numpy as np

from gridpilot.cascade import CascadeSim
from gridpilot.grid import load_case


def test_overloaded_line_trips_after_delay_not_before():
    sim = CascadeSim(load_case(), trip_delay=3, instant_trip_pct=140.0)
    # nudge one line just past its limit: over 100% so the timer runs, but
    # below the instantaneous-overcurrent threshold so it does not trip at once
    idx = int(sim.net.line.index[int(np.argmax(sim.loadings()))])
    sim.net.line.at[idx, "max_i_ka"] *= 0.9
    sim.solve()
    loading = sim.loadings()[sim.net.line.index.get_loc(idx)]
    assert 100 < loading < 140

    assert sim.step() == []          # tick 1: over limit, not yet
    assert sim.step() == []          # tick 2: still counting
    assert idx in sim.step()         # tick 3: protection acts


def test_severe_overload_trips_immediately():
    sim = CascadeSim(load_case(), trip_delay=5, instant_trip_pct=120.0)
    idx = int(sim.net.line.index[int(np.argmax(sim.loadings()))])
    sim.net.line.at[idx, "max_i_ka"] *= 0.1
    sim.solve()
    assert idx in sim.step()


def test_shed_load_reduces_served_load():
    sim = CascadeSim(load_case())
    bus = int(sim.net.load.sort_values("p_mw", ascending=False).iloc[0].bus)
    before = sim.metrics().load_served_mw
    got = sim.shed_load(bus, 40.0)
    assert got > 0
    assert sim.metrics().load_served_mw < before


def test_islanded_load_counts_as_lost():
    """A pocket with no generation must show up as lost load, not silently
    stay 'served' because its bus went out of service."""
    sim = CascadeSim(load_case())
    net = sim.net
    # isolate a load bus that has no local generation
    gen_buses = set(net.gen.bus) | set(net.ext_grid.bus)
    target = next(
        int(b) for b in net.load.bus.unique()
        if b not in gen_buses and net.load[net.load.bus == b].p_mw.sum() > 5
    )
    for idx in net.line.index[(net.line.from_bus == target) | (net.line.to_bus == target)]:
        net.line.at[idx, "in_service"] = False
    for idx in net.trafo.index[(net.trafo.hv_bus == target) | (net.trafo.lv_bus == target)]:
        net.trafo.at[idx, "in_service"] = False
    sim.solve()
    assert sim.metrics().load_lost_mw > 0


def test_clone_is_independent():
    sim = CascadeSim(load_case())
    twin = sim.clone()
    idx = int(twin.net.line.index[0])
    twin.trip_line(idx)
    assert sim.net.line.at[idx, "in_service"]
    assert not twin.net.line.at[idx, "in_service"]


def test_generator_capability_is_spinning_reserve_not_nameplate():
    sim = CascadeSim(load_case())
    g = int(sim.net.gen.index[int(np.argmax(sim.net.gen.p_mw.values))])
    dispatch = float(sim.net.gen.at[g, "p_mw"])
    cap = float(sim._gen_capability([g])[0])
    assert dispatch <= cap <= dispatch * 1.2

"""How much does moving a generator relieve a given line?

Real control rooms answer this with PTDF sensitivity factors. Rather than
reach into pandapower's internal branch ordering to pull the PTDF matrix out
(easy to misalign, and a silent misalignment would produce confidently wrong
advice), this measures the same quantity by finite difference: nudge a
generator on a cloned grid, re-solve, and read the change in the target line's
flow. A handful of DC solves, and it cannot be wrong about which line is which.
"""

from __future__ import annotations

import numpy as np

from gridpilot.cascade import CascadeSim

PROBE_MW = 50.0


def candidate_gens(sim: CascadeSim, line: int, hops: int = 3, top_n: int = 14) -> list[int]:
    """Generators worth probing: the electrically nearby ones plus the biggest
    in the system, since distant large units still shift flows."""
    net = sim.net
    seen = {int(net.line.at[line, "from_bus"]), int(net.line.at[line, "to_bus"])}
    frontier = set(seen)
    live = net.line[net.line.in_service]
    pairs = list(zip(live.from_bus.astype(int), live.to_bus.astype(int)))
    for _ in range(hops):
        nxt = set()
        for f, t in pairs:
            if f in frontier:
                nxt.add(t)
            if t in frontier:
                nxt.add(f)
        frontier = nxt - seen
        seen |= nxt

    gens = net.gen[net.gen.in_service]
    near = gens.index[gens.bus.isin(seen)].tolist()
    big = gens.p_mw.sort_values(ascending=False).index.tolist()
    out: list[int] = []
    for g in near + big:
        if g not in out:
            out.append(int(g))
        if len(out) >= top_n:
            break
    return out


def line_flow(sim: CascadeSim, line: int) -> float:
    v = sim.net.res_line.p_from_mw.get(line)
    return float(np.nan_to_num(v)) if v is not None else 0.0


def sensitivities(sim: CascadeSim, line: int, gens: list[int] | None = None,
                  probe_mw: float = PROBE_MW) -> list[dict]:
    """d(loading %) / d(gen MW) for the target line, one finite-difference
    solve per generator. Negative means raising that generator relieves the line.
    """
    if gens is None:
        gens = candidate_gens(sim, line)
    load0 = sim.loadings()
    k = sim.net.line.index.get_loc(line)
    base_loading = float(load0[k])

    out = []
    for g in gens:
        twin = sim.clone()
        cur = float(twin.net.gen.at[g, "p_mw"])
        cap = float(twin._gen_capability([g])[0])
        # probe in whichever direction the unit actually has room to move
        delta = probe_mw if cap - cur >= probe_mw else -min(probe_mw, cur)
        if abs(delta) < 1.0:
            continue
        twin.net.gen.at[g, "p_mw"] = cur + delta
        twin.solve()
        new_loading = float(twin.loadings()[k])
        out.append({
            "gen": int(g),
            "bus": int(sim.net.gen.at[g, "bus"]),
            "current_mw": round(cur, 1),
            "max_mw": round(cap, 1),
            "d_loading_per_mw": round((new_loading - base_loading) / delta, 4),
        })
    out.sort(key=lambda d: d["d_loading_per_mw"])
    return out


def relief_options(sim: CascadeSim, line: int, top: int = 5) -> dict:
    """Ranked redispatch options for relieving one line, as an operator would
    want them: which units to raise, which to lower, and the sensitivity."""
    load = sim.loadings()
    k = sim.net.line.index.get_loc(line)
    loading = float(load[k])
    sens = sensitivities(sim, line)
    raise_ = [s for s in sens if s["d_loading_per_mw"] < -1e-4][:top]
    lower = [s for s in reversed(sens) if s["d_loading_per_mw"] > 1e-4][:top]
    excess = max(loading - 100.0, 0.0)
    for s in raise_ + lower:
        per_mw = abs(s["d_loading_per_mw"])
        s["mw_to_clear_overload"] = round(excess / per_mw, 1) if per_mw > 1e-6 else None
    return {
        "line": int(line),
        "loading_pct": round(loading, 1),
        "excess_pct": round(excess, 1),
        "raise_these_to_relieve": raise_,
        "lower_these_to_relieve": lower,
        "note": "d_loading_per_mw is the change in this line's loading percent per MW "
                "of generator output; raising a unit with a negative value relieves it",
    }

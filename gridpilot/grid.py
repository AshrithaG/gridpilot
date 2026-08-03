"""IEEE 118-bus case setup: thermal limits, layout coordinates, state snapshots.

pandapower ships case118 with thermal limits so loose that nothing ever
overloads (base case peaks at 4.5% loading). Cascading failures need limits
that bind, so limits are rescaled to put the base case at a target
utilization. Coordinates are laid out once and cached, since the raw case has
no geodata.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", message=".*numba.*")

import pandapower as pp  # noqa: E402
import pandapower.networks as nw  # noqa: E402

DATA = Path(__file__).parent / "data"
LAYOUT_CACHE = DATA / "case118_layout.json"
LIMIT_CACHE = DATA / "case118_limits.json"

# headroom above the worst single-contingency flow. 1.05 leaves the grid N-1
# secure but only just, so double contingencies bite.
N1_MARGIN = 1.05

# reserve available from the slack bus, as a multiple of its base dispatch
SLACK_HEADROOM = 1.25

# Spinning reserve as a fraction of current dispatch. On the timescale a
# cascade unfolds (seconds to minutes) a generator can only contribute its
# spinning reserve, not its nameplate rating -- which is exactly why islanding
# forces load shedding in real events.
RESERVE_FRAC = 0.10


def _bare_case():
    """case118 with everything in service and untouched physics."""
    net = nw.case118()
    net.line["in_service"] = True
    if len(net.trafo):
        net.trafo["in_service"] = True
    net.load["scaling"] = 1.0
    return net


def _n1_secure_limits(margin: float = N1_MARGIN) -> dict:
    """Size every line for the worst flow it sees across all single-line
    contingencies, which is how transmission planning actually sets ratings.
    The result is an N-1 secure grid: no single trip overloads anything, but
    double contingencies do. Cached, since it costs one solve per line.

    Transformer ratings are left alone: their impedance is defined relative
    to sn_mva, so rescaling the rating would change the power flow itself.
    """
    if LIMIT_CACHE.exists():
        return json.loads(LIMIT_CACHE.read_text())

    net = _bare_case()
    pp.rundcpp(net)
    worst = np.nan_to_num(net.res_line.i_ka.values, nan=0.0)

    for idx in net.line.index:
        trial = _bare_case()
        trial.line.at[idx, "in_service"] = False
        try:
            pp.rundcpp(trial, check_connectivity=True)
        except Exception:
            continue
        flows = np.nan_to_num(
            trial.res_line.i_ka.reindex(trial.line.index).values, nan=0.0
        )
        flows[trial.line.index.get_loc(idx)] = 0.0  # the tripped line carries nothing
        worst = np.maximum(worst, flows)

    floor = float(np.percentile(worst[worst > 0], 5)) if (worst > 0).any() else 0.05
    limits = np.maximum(worst, floor) * margin
    out = {"max_i_ka": [float(v) for v in limits], "margin": margin}
    DATA.mkdir(parents=True, exist_ok=True)
    LIMIT_CACHE.write_text(json.dumps(out))
    return out


def load_case(stress: float = 1.0):
    """Load the case with N-1 secure thermal limits.

    `stress` scales load and generation together to model a heavily loaded
    system; above ~1.0 the grid loses its N-1 security, which is a realistic
    knob (grids are planned for peak, and cascades happen at peak).
    """
    net = _bare_case()
    net.line["max_i_ka"] = _n1_secure_limits()["max_i_ka"]
    if stress != 1.0:
        net.load["p_mw"] = net.load.p_mw.values * stress
        net.gen["p_mw"] = net.gen.p_mw.values * stress

    # The slack bus stands for the rest of the interconnection. Left unbounded
    # it makes the main island immune to generation deficit, so islanding could
    # never cost load there -- which is the opposite of how real cascades hurt.
    # Give it a finite reserve above its base dispatch instead.
    pp.rundcpp(net)
    slack_base = float(np.nan_to_num(net.res_ext_grid.p_mw.values).sum())
    net["_ext_grid_cap_mw"] = max(slack_base, 0.0) * SLACK_HEADROOM

    net["_original_load_mw"] = net.load.p_mw.copy()
    net["_original_gen_mw"] = net.gen.p_mw.copy()
    return net


def layout(net) -> dict[int, tuple[float, float]]:
    """Bus coordinates in [0,1]^2. Computed once with a force-directed layout
    and cached so the map is stable across runs."""
    if LAYOUT_CACHE.exists():
        raw = json.loads(LAYOUT_CACHE.read_text())
        return {int(k): tuple(v) for k, v in raw.items()}

    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(net.bus.index.tolist())
    g.add_edges_from(zip(net.line.from_bus, net.line.to_bus))
    g.add_edges_from(zip(net.trafo.hv_bus, net.trafo.lv_bus))
    pos = nx.kamada_kawai_layout(g)

    xs = np.array([p[0] for p in pos.values()])
    ys = np.array([p[1] for p in pos.values()])
    span = max(xs.ptp(), ys.ptp()) or 1.0
    out = {
        int(b): (float((p[0] - xs.min()) / span), float((p[1] - ys.min()) / span))
        for b, p in pos.items()
    }
    LAYOUT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT_CACHE.write_text(json.dumps(out))
    return out


@dataclass
class Event:
    tick: int
    kind: str  # line_trip | island | load_shed | redispatch | blackout
    detail: str
    mw: float = 0.0


@dataclass
class Metrics:
    load_served_mw: float = 0.0
    load_lost_mw: float = 0.0
    buses_energized: int = 0
    buses_dark: int = 0
    lines_tripped: int = 0
    max_loading_pct: float = 0.0
    overloaded_lines: int = 0
    islands: int = 1
    shed_by_operator_mw: float = 0.0
    events: list[Event] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "events"}
        d["events"] = [e.__dict__ for e in self.events]
        return d

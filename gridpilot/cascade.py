"""Cascading-failure engine.

One tick = solve DC power flow, then let protection relays act. A line over
its thermal limit trips after `trip_delay` consecutive ticks (or immediately
past `instant_trip_pct`, standing in for instantaneous overcurrent
protection). Tripping redistributes flow, which can overload the next line —
that feedback loop is the cascade, and the delay is the window an operator
(or an agent) has to intervene.

Islanding is handled explicitly: when the topology splits, each island needs
its own slack and its own power balance. Islands with no generation go dark;
islands short on generation shed load, standing in for under-frequency load
shedding.
"""

from __future__ import annotations

import copy
import warnings

import networkx as nx
import numpy as np

warnings.filterwarnings("ignore", message=".*numba.*")

import pandapower as pp  # noqa: E402

from gridpilot.grid import RESERVE_FRAC, Event, Metrics, load_case  # noqa: E402


class CascadeSim:
    def __init__(self, net=None, trip_delay: int = 2, instant_trip_pct: float = 140.0):
        self.net = net if net is not None else load_case()
        self.trip_delay = trip_delay
        self.instant_trip_pct = instant_trip_pct
        self.tick = 0
        self.events: list[Event] = []
        self._over_count = np.zeros(len(self.net.line), dtype=int)
        self._temp_slacks: list[int] = []
        self._operator_shed_mw = 0.0
        self.dark_buses: set[int] = set()
        self.solve()

    # ---------- topology ----------

    def _graph(self) -> nx.Graph:
        net = self.net
        g = nx.Graph()
        g.add_nodes_from(net.bus.index[net.bus.in_service].tolist())
        live = net.line[net.line.in_service]
        for f, t in zip(live.from_bus, live.to_bus):
            if f in g and t in g:
                g.add_edge(int(f), int(t))
        if len(net.trafo):
            lt = net.trafo[net.trafo.in_service]
            for f, t in zip(lt.hv_bus, lt.lv_bus):
                if f in g and t in g:
                    g.add_edge(int(f), int(t))
        return g

    def _gen_capability(self, gen_idx) -> np.ndarray:
        """Deliverable output on the cascade timescale: current dispatch plus
        spinning reserve, capped by the nameplate rating. Using max_p_mw here
        would let islands magically self-supply."""
        gens = self.net.gen.loc[gen_idx]
        reachable = gens.p_mw.values * (1.0 + RESERVE_FRAC)
        nameplate = gens.get("max_p_mw")
        if nameplate is not None:
            nameplate = nameplate.fillna(gens.p_mw * (1.0 + RESERVE_FRAC)).values
            reachable = np.minimum(reachable, nameplate)
        return np.maximum(reachable, 0.0)

    def _balance_islands(self) -> list[set[int]]:
        """Give every island a slack, black out the ones without generation,
        and shed load where generation cannot cover it."""
        net = self.net
        for idx in self._temp_slacks:
            if idx in net.ext_grid.index:
                net.ext_grid.drop(idx, inplace=True)
        self._temp_slacks = []

        islands = [set(c) for c in nx.connected_components(self._graph())]
        # isolated (fully disconnected) buses still count as their own island
        for b in net.bus.index[net.bus.in_service]:
            if not any(b in isl for isl in islands):
                islands.append({int(b)})

        dark: set[int] = set()
        for isl in islands:
            slack_here = net.ext_grid[
                net.ext_grid.in_service & net.ext_grid.bus.isin(isl)
            ]
            gens_here = net.gen.index[net.gen.in_service & net.gen.bus.isin(isl)]
            loads_here = net.load.index[net.load.in_service & net.load.bus.isin(isl)]
            load_mw = float(net.load.loc[loads_here].p_mw.sum()) if len(loads_here) else 0.0

            if len(slack_here) == 0 and len(gens_here) == 0:
                # no generation at all: the island is dead. Its loads have to
                # come out of service too, or they still count as served.
                if load_mw > 0:
                    self.events.append(
                        Event(self.tick, "blackout",
                              f"island of {len(isl)} buses lost all generation", load_mw)
                    )
                net.load.loc[loads_here, "in_service"] = False
                dark |= isl
                continue

            capability = float(self._gen_capability(gens_here).sum()) if len(gens_here) else 0.0
            if len(slack_here) == 0:
                # promote the largest generator in the island to slack
                caps = self._gen_capability(gens_here)
                host = int(net.gen.loc[gens_here[int(np.argmax(caps))]].bus)
                idx = pp.create_ext_grid(net, bus=host, vm_pu=1.0,
                                         name=f"island_slack_t{self.tick}")
                self._temp_slacks.append(idx)
            else:
                capability += float(net.get("_ext_grid_cap_mw", 0.0))

            if load_mw > capability and load_mw > 0:
                keep = capability / load_mw
                shed = load_mw - capability
                net.load.loc[loads_here, "p_mw"] *= keep
                self.events.append(
                    Event(self.tick, "load_shed",
                          f"under-frequency shedding in island of {len(isl)} buses", shed)
                )

            if len(gens_here):
                # dispatch generation to match island load
                cur = float(net.gen.loc[gens_here].p_mw.sum())
                target = min(load_mw, capability)
                if len(slack_here) == 0 and cur > 0 and target > 0:
                    net.gen.loc[gens_here, "p_mw"] *= target / cur

        for b in dark:
            net.bus.at[b, "in_service"] = False
        self.dark_buses |= dark
        return islands

    # ---------- solve ----------

    def solve(self) -> None:
        islands = self._balance_islands()
        self._islands = islands
        try:
            pp.rundcpp(self.net, check_connectivity=True)
            self._solved = True
        except Exception:
            self._solved = False

    def loadings(self) -> np.ndarray:
        """Line loading in percent; 0 for out-of-service or unsolved lines."""
        if not self._solved or "loading_percent" not in self.net.res_line:
            return np.zeros(len(self.net.line))
        v = self.net.res_line.loading_percent.reindex(self.net.line.index).fillna(0.0).values
        return np.where(self.net.line.in_service.values, v, 0.0)

    def step(self) -> list[int]:
        """Let protection act: trip lines that have been over limit long enough,
        then re-solve. Returns the indices of lines that tripped. The caller
        owns the clock, so an operator can be given a window between a
        disturbance and the relays responding to it."""
        load = self.loadings()
        over = load > 100.0
        self._over_count = np.where(over, self._over_count + 1, 0)

        trip_mask = ((self._over_count >= self.trip_delay) | (load > self.instant_trip_pct)) \
            & self.net.line.in_service.values
        tripped = self.net.line.index[trip_mask].tolist()
        for idx in tripped:
            self.net.line.at[idx, "in_service"] = False
            self._over_count[self.net.line.index.get_loc(idx)] = 0
            self.events.append(
                Event(self.tick, "line_trip",
                      f"line {idx} ({int(self.net.line.at[idx,'from_bus'])}-"
                      f"{int(self.net.line.at[idx,'to_bus'])}) tripped at "
                      f"{load[self.net.line.index.get_loc(idx)]:.0f}% loading")
            )
        if tripped:
            self.solve()
        return tripped

    def settled(self) -> bool:
        """True when no line is over its limit, so no further trips are coming."""
        return not (self.loadings() > 100.0).any()

    # ---------- operator actions ----------

    def trip_line(self, idx: int, reason: str = "manual") -> None:
        if idx not in self.net.line.index:
            raise ValueError(f"no line {idx}")
        self.net.line.at[idx, "in_service"] = False
        self.events.append(Event(self.tick, "line_trip", f"line {idx} opened ({reason})"))
        self.solve()

    def close_line(self, idx: int) -> None:
        if idx not in self.net.line.index:
            raise ValueError(f"no line {idx}")
        self.net.line.at[idx, "in_service"] = True
        self.events.append(Event(self.tick, "line_trip", f"line {idx} reclosed"))
        self.solve()

    def shed_load(self, bus: int, mw: float) -> float:
        """Curtail up to `mw` of load at a bus. Returns MW actually shed."""
        rows = self.net.load.index[(self.net.load.bus == bus) & self.net.load.in_service]
        if not len(rows):
            raise ValueError(f"no in-service load at bus {bus}")
        avail = float(self.net.load.loc[rows].p_mw.sum())
        shed = float(min(max(mw, 0.0), avail))
        if avail > 0:
            self.net.load.loc[rows, "p_mw"] *= (avail - shed) / avail
        self._operator_shed_mw += shed
        self.events.append(Event(self.tick, "load_shed", f"operator shed at bus {bus}", shed))
        self.solve()
        return shed

    def redispatch(self, gen: int, mw: float) -> float:
        """Set a generator's output, clipped to its capability."""
        if gen not in self.net.gen.index:
            raise ValueError(f"no generator {gen}")
        cap = float(self._gen_capability([gen])[0])
        target = float(np.clip(mw, 0.0, cap))
        before = float(self.net.gen.at[gen, "p_mw"])
        self.net.gen.at[gen, "p_mw"] = target
        self.events.append(
            Event(self.tick, "redispatch",
                  f"gen {gen} at bus {int(self.net.gen.at[gen,'bus'])}: "
                  f"{before:.0f} -> {target:.0f} MW", target - before)
        )
        self.solve()
        return target

    # ---------- reporting ----------

    def clone(self) -> CascadeSim:
        """Deep copy for what-if analysis. This is what makes
        simulate-before-apply possible."""
        twin = copy.copy(self)
        twin.net = copy.deepcopy(self.net)
        twin.events = list(self.events)
        twin._over_count = self._over_count.copy()
        twin._temp_slacks = list(self._temp_slacks)
        twin.dark_buses = set(self.dark_buses)
        return twin

    def metrics(self) -> Metrics:
        net = self.net
        served = float(net.load[net.load.in_service].p_mw.sum())
        original = float(net["_original_load_mw"].sum())
        load = self.loadings()
        live_bus = net.bus.in_service
        return Metrics(
            load_served_mw=round(served, 1),
            load_lost_mw=round(original - served, 1),
            buses_energized=int(live_bus.sum()),
            buses_dark=int((~live_bus).sum()),
            lines_tripped=int((~net.line.in_service).sum()),
            max_loading_pct=round(float(load.max()) if len(load) else 0.0, 1),
            overloaded_lines=int((load > 100.0).sum()),
            islands=len(getattr(self, "_islands", [1])),
            shed_by_operator_mw=round(self._operator_shed_mw, 1),
            events=list(self.events),
        )

    def snapshot(self, include_events: int = 12) -> dict:
        """Compact state for the UI and the agent."""
        net = self.net
        load = self.loadings()
        lines = [
            {
                "id": int(i),
                "from": int(net.line.at[i, "from_bus"]),
                "to": int(net.line.at[i, "to_bus"]),
                "loading": round(float(load[k]), 1),
                "in_service": bool(net.line.at[i, "in_service"]),
            }
            for k, i in enumerate(net.line.index)
        ]
        load_by_bus = net.load[net.load.in_service].groupby("bus").p_mw.sum().to_dict()
        buses = [
            {
                "id": int(b),
                "energized": bool(net.bus.at[b, "in_service"]),
                "load_mw": round(float(load_by_bus.get(b, 0.0)), 1),
            }
            for b in net.bus.index
        ]
        m = self.metrics()
        return {
            "tick": self.tick,
            "settled": self.settled(),
            "lines": lines,
            "buses": buses,
            "metrics": {k: v for k, v in m.as_dict().items() if k != "events"},
            "events": [e.__dict__ for e in self.events[-include_events:]],
        }

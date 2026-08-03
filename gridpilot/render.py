"""Static grid renderings for the README.

The live UI is the demo, but a screenshot is not reproducible. This draws the
same picture from the same state with matplotlib, so the hero image can be
regenerated from the code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from gridpilot.cascade import CascadeSim
from gridpilot.grid import layout, load_case
from gridpilot.scenarios import make_scenario

FIGURES = Path("results/figures")

BG = "#0f1115"
PANEL = "#171a21"
TEXT = "#e6e8ec"
MUTED = "#9aa3b2"


def color_for(pct: float) -> str:
    if pct >= 100:
        return "#d64545"
    if pct >= 90:
        return "#d9772e"
    if pct >= 70:
        return "#c8a02a"
    return "#3f6f4a"


def draw(ax, sim: CascadeSim, title: str, subtitle: str = ""):
    pos = layout(sim.net)
    net = sim.net
    load = sim.loadings()
    ax.set_facecolor(PANEL)

    for i in net.trafo.index:
        a, b = int(net.trafo.at[i, "hv_bus"]), int(net.trafo.at[i, "lv_bus"])
        if a in pos and b in pos:
            ax.plot(*zip(pos[a], pos[b]), color="#39404e", lw=0.7, ls=":", zorder=1)

    for k, i in enumerate(net.line.index):
        a, b = int(net.line.at[i, "from_bus"]), int(net.line.at[i, "to_bus"])
        if a not in pos or b not in pos:
            continue
        xs, ys = zip(pos[a], pos[b])
        if not net.line.at[i, "in_service"]:
            ax.plot(xs, ys, color="#5b6270", lw=0.9, ls=(0, (3, 3)), zorder=2)
        else:
            pct = float(load[k])
            ax.plot(xs, ys, color=color_for(pct),
                    lw=2.6 if pct >= 100 else 1.8 if pct >= 90 else 1.1, zorder=3)

    live = net.bus.in_service
    gen_buses = set(net.gen.bus) | set(net.ext_grid.bus)
    for b in net.bus.index:
        if b not in pos:
            continue
        x, y = pos[b]
        if not live[b]:
            ax.plot(x, y, "o", ms=4, color="#6b7280", alpha=0.5, zorder=4)
        elif b in gen_buses:
            ax.plot(x, y, "o", ms=3.6, color="#7fb3f0", zorder=5)
        else:
            ax.plot(x, y, "o", ms=2.0, color="#4b525f", zorder=4)

    ax.set_title(title, color=TEXT, fontsize=12, pad=8)
    if subtitle:
        ax.text(0.5, -0.04, subtitle, transform=ax.transAxes, ha="center",
                va="top", color=MUTED, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#2a2f3a")


def hero(seed: int = 9, out: Path | None = None):
    """Before/after on one incident, with the agent's own numbers."""
    from gridpilot.policies import RedispatchRelief
    from gridpilot.tools import what_if

    sc = make_scenario(seed)
    sim = CascadeSim(load_case(sc.stress))
    for line in sc.trips_at(0):
        sim.trip_line(line, reason="scenario")

    before_over = int((sim.loadings() > 100).sum())
    before_forecast = what_if(sim, [])["would_lose_mw"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6.6), facecolor=BG)
    draw(axes[0], sim, f"{sc.id}: the moment after the fault",
         f"{before_over} lines over their thermal limit  ·  "
         f"{before_forecast:.0f} MW will be lost if nobody acts")

    pol = RedispatchRelief()
    taken = pol(sim)
    after_over = int((sim.loadings() > 100).sum())
    after_forecast = what_if(sim, [])["would_lose_mw"]
    moves = sum(1 for t in taken if "error" not in t)

    draw(axes[1], sim, "after the operator redispatches",
         f"{after_over} lines overloaded  ·  {after_forecast:.0f} MW forecast lost  ·  "
         f"{moves} generator moves, no load shed")

    handles = [
        Line2D([], [], color="#3f6f4a", lw=2, label="under 70%"),
        Line2D([], [], color="#c8a02a", lw=2, label="70-90%"),
        Line2D([], [], color="#d9772e", lw=2, label="90-100%"),
        Line2D([], [], color="#d64545", lw=2.6, label="overloaded"),
        Line2D([], [], color="#5b6270", lw=1, ls="--", label="tripped"),
        Line2D([], [], color="#7fb3f0", marker="o", ls="", label="generator bus"),
    ]
    leg = fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False,
                     fontsize=9, bbox_to_anchor=(0.5, 0.005))
    for t in leg.get_texts():
        t.set_color(MUTED)

    fig.suptitle("GridPilot — IEEE 118-bus cascading failure, operator in the loop",
                 color=TEXT, fontsize=13, y=0.97)
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    out = out or (FIGURES / "hero.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=BG)
    print(f"wrote {out}  (before: {before_over} overloaded / {before_forecast:.0f} MW at risk, "
          f"after: {after_over} / {after_forecast:.0f} MW)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=9)
    args = ap.parse_args()
    hero(args.seed)

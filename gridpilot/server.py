"""Web backend: serve the map, stream the incident, run the operator live.

One websocket per session. The client can trip a line by clicking it, start a
scripted scenario, or hand control to the agent and watch its tool calls arrive
as they happen.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gridpilot.cascade import CascadeSim
from gridpilot.grid import layout, load_case
from gridpilot.policies import RedispatchRelief
from gridpilot.scenarios import make_scenario
from gridpilot.tools import PROTECTED_BUSES, grid_state, what_if

STATIC = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="GridPilot")

# ---------------------------------------------------------------- public demo
# The heuristic and manual policies are pure simulation and cost nothing to run,
# so an anonymous visitor gets a working demo. Only the LLM agent needs a key,
# and when none is configured it says so instead of failing with a stack trace.
AGENT_ENABLED = bool(os.environ.get("ANTHROPIC_API_KEY"))

# A visit and run count, kept in a file so it survives process restarts. Free
# tiers usually have ephemeral disks, so this resets on redeploy; it is a demo
# counter, not analytics.
_COUNTS_PATH = Path(os.environ.get("GRIDPILOT_COUNTS", "/tmp/gridpilot_counts.json"))
_counts_lock = threading.Lock()


def _bump(key: str) -> dict:
    with _counts_lock:
        try:
            counts = json.loads(_COUNTS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            counts = {}
        counts[key] = int(counts.get(key, 0)) + 1
        try:
            tmp = _COUNTS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(counts))
            tmp.replace(_COUNTS_PATH)
        except OSError:
            pass  # a demo counter is never worth failing a request over
        return counts


def _read_counts() -> dict:
    try:
        return json.loads(_COUNTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def topology(sim: CascadeSim) -> dict:
    pos = layout(sim.net)
    net = sim.net
    return {
        "buses": [
            {
                "id": int(b),
                "x": pos.get(int(b), (0.5, 0.5))[0],
                "y": pos.get(int(b), (0.5, 0.5))[1],
                "load_mw": round(float(net.load[net.load.bus == b].p_mw.sum()), 1),
                "gen_mw": round(float(net.gen[net.gen.bus == b].p_mw.sum()), 1),
                "slack": bool((net.ext_grid.bus == b).any()),
                "protected": int(b) in PROTECTED_BUSES,
            }
            for b in net.bus.index
        ],
        "lines": [
            {"id": int(i), "from": int(net.line.at[i, "from_bus"]),
             "to": int(net.line.at[i, "to_bus"])}
            for i in net.line.index
        ],
        "trafos": [
            {"from": int(net.trafo.at[i, "hv_bus"]), "to": int(net.trafo.at[i, "lv_bus"])}
            for i in net.trafo.index
        ],
    }


class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.sim = CascadeSim(load_case())
        self.scenario = None
        self.tick = 0
        self.log: list[dict] = []

    async def send_state(self, note: str | None = None):
        snap = self.sim.snapshot()
        snap["forecast"] = what_if(self.sim, [])
        snap["note"] = note
        snap["log"] = self.log[-40:]
        await self.ws.send_text(json.dumps({"type": "state", "payload": snap}))

    def note(self, kind: str, text: str):
        self.log.append({"tick": self.tick, "kind": kind, "text": text})

    def reset(self, stress: float = 1.0):
        self.sim = CascadeSim(load_case(stress))
        self.tick = 0
        self.log = []

    async def advance(self, ticks: int = 1):
        for _ in range(ticks):
            self.tick += 1
            self.sim.tick = self.tick
            if self.scenario:
                for line in self.scenario.trips_at(self.tick):
                    if self.sim.net.line.at[line, "in_service"]:
                        self.sim.trip_line(line, reason="scenario")
                        self.note("event", f"scenario trips line {line}")
            tripped = self.sim.step()
            if tripped:
                self.note("protection", f"protection tripped lines {tripped}")
            await self.send_state()
            await asyncio.sleep(0.05)

    async def run_agent(self, model: str):
        from gridpilot.agent import AgentOperator

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_turn(turn):
            loop.call_soon_threadsafe(queue.put_nowait, turn)

        agent = AgentOperator(model=model, max_turns=8)
        agent.on_turn = on_turn

        async def drain():
            while True:
                turn = await queue.get()
                if turn is None:
                    return
                await self.ws.send_text(json.dumps({"type": "agent_turn", "payload": {
                    "tick": turn.tick, "tool": turn.tool,
                    "args": turn.args, "result": turn.result[:400]}}))

        drainer = asyncio.create_task(drain())
        await self.ws.send_text(json.dumps({"type": "agent_status",
                                            "payload": {"state": "thinking"}}))
        try:
            await loop.run_in_executor(None, agent, self.sim)
        finally:
            queue.put_nowait(None)
            await drainer
        self.note("agent", f"agent finished ({agent.stats['turns']} turns, "
                           f"{agent.stats['applied']} actions applied)")
        await self.ws.send_text(json.dumps({"type": "agent_status", "payload": {
            "state": "idle", "stats": agent.stats}}))
        await self.send_state()

    async def run_heuristic(self):
        pol = RedispatchRelief()
        loop = asyncio.get_running_loop()
        taken = await loop.run_in_executor(None, pol, self.sim)
        self.note("heuristic", f"redispatch heuristic took {len(taken)} action(s)")
        await self.send_state()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _bump("sessions")
    sess = Session(ws)
    await ws.send_text(json.dumps({"type": "topology", "payload": topology(sess.sim)}))
    await sess.send_state("ready")
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            if cmd == "trip_line":
                line = int(msg["line"])
                if sess.sim.net.line.at[line, "in_service"]:
                    sess.sim.trip_line(line, reason="manual")
                    sess.note("event", f"you opened line {line}")
                    await sess.send_state(f"line {line} opened")
            elif cmd == "step":
                await sess.advance(int(msg.get("ticks", 1)))
            elif cmd == "settle":
                for _ in range(12):
                    if sess.sim.settled():
                        break
                    await sess.advance(1)
            elif cmd == "scenario":
                seed = int(msg.get("seed", 5))
                sc = make_scenario(seed)
                sess.reset(sc.stress)
                sess.scenario = sc
                sess.note("event", sc.description)
                await ws.send_text(json.dumps({"type": "scenario", "payload": {
                    "id": sc.id, "kind": sc.kind, "description": sc.description}}))
                for line in sc.trips_at(0):
                    sess.sim.trip_line(line, reason="scenario")
                await sess.send_state(sc.description)
            elif cmd == "reset":
                sess.scenario = None
                sess.reset()
                await sess.send_state("grid restored")
            elif cmd == "agent":
                if not AGENT_ENABLED:
                    sess.note("agent", "the LLM operator is disabled on this public "
                                       "demo (no API key configured). The redispatch "
                                       "heuristic and manual line tripping both work.")
                    await ws.send_text(json.dumps({"type": "agent_status",
                                                   "payload": {"state": "disabled"}}))
                    await sess.send_state()
                else:
                    _bump("agent_runs")
                    await sess.run_agent(msg.get("model", "claude-haiku-4-5-20251001"))
            elif cmd == "heuristic":
                _bump("heuristic_runs")
                await sess.run_heuristic()
            elif cmd == "state":
                await sess.send_state()
    except WebSocketDisconnect:
        return


@app.get("/api/scenarios")
def list_scenarios():
    from gridpilot.benchmark import load as load_bench

    try:
        b = load_bench()
    except FileNotFoundError:
        return {"damaging": [], "benign": []}
    keep = ("seed", "id", "kind", "description", "no_action_load_lost_mw")
    return {k: [{f: r[f] for f in keep} for r in v[:20]]
            for k, v in b.items() if isinstance(v, list)}


@app.get("/api/state")
def one_shot_state():
    sim = CascadeSim(load_case())
    return {"topology": topology(sim), "state": grid_state(sim)}


@app.get("/api/config")
def config():
    """What this deployment can actually do, so the UI can say so up front."""
    return {"agent_enabled": AGENT_ENABLED}


@app.get("/api/stats")
def stats():
    c = _read_counts()
    return {"sessions": c.get("sessions", 0),
            "heuristic_runs": c.get("heuristic_runs", 0),
            "agent_runs": c.get("agent_runs", 0)}


if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
else:  # pragma: no cover
    @app.get("/")
    def index():
        return HTMLResponse("<h1>GridPilot</h1><p>frontend not built</p>")

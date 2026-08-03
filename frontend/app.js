const SVG_NS = "http://www.w3.org/2000/svg";
const W = 1000, H = 640, PAD = 34;

const el = (id) => document.getElementById(id);
const map = el("map");
let topo = null;
let ws = null;

function project(x, y) {
  return [PAD + x * (W - 2 * PAD), PAD + (1 - y) * (H - 2 * PAD)];
}

function loadingColor(pct) {
  if (pct >= 100) return "#d64545";
  if (pct >= 90) return "#d9772e";
  if (pct >= 70) return "#c8a02a";
  return "#3f6f4a";
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "topology") { topo = msg.payload; drawBase(); }
    else if (msg.type === "state") render(msg.payload);
    else if (msg.type === "agent_turn") addTurn(msg.payload);
    else if (msg.type === "agent_status") setAgentState(msg.payload);
    else if (msg.type === "scenario") announce(msg.payload);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function drawBase() {
  map.textContent = "";
  const gLines = document.createElementNS(SVG_NS, "g");
  const gBuses = document.createElementNS(SVG_NS, "g");
  gLines.id = "g-lines";
  gBuses.id = "g-buses";
  map.append(gLines, gBuses);

  const pos = {};
  topo.buses.forEach((b) => { pos[b.id] = project(b.x, b.y); });
  topo._pos = pos;

  // transformers first, as static background
  topo.trafos.forEach((t) => {
    const [x1, y1] = pos[t.from], [x2, y2] = pos[t.to];
    const l = document.createElementNS(SVG_NS, "line");
    l.setAttribute("x1", x1); l.setAttribute("y1", y1);
    l.setAttribute("x2", x2); l.setAttribute("y2", y2);
    l.setAttribute("stroke", "#39404e");
    l.setAttribute("stroke-width", 1.5);
    l.setAttribute("stroke-dasharray", "1 3");
    gLines.appendChild(l);
  });

  topo.lines.forEach((ln) => {
    const [x1, y1] = pos[ln.from], [x2, y2] = pos[ln.to];
    const hit = document.createElementNS(SVG_NS, "line");
    hit.setAttribute("x1", x1); hit.setAttribute("y1", y1);
    hit.setAttribute("x2", x2); hit.setAttribute("y2", y2);
    hit.setAttribute("class", "line-hit");
    hit.addEventListener("click", () => send({ cmd: "trip_line", line: ln.id }));
    hit.appendChild(titleFor(ln.id, null));

    const vis = document.createElementNS(SVG_NS, "line");
    vis.setAttribute("x1", x1); vis.setAttribute("y1", y1);
    vis.setAttribute("x2", x2); vis.setAttribute("y2", y2);
    vis.setAttribute("class", "line-vis");
    vis.setAttribute("stroke", "#3f6f4a");
    vis.setAttribute("stroke-width", 2);
    vis.setAttribute("stroke-linecap", "round");
    vis.id = `line-${ln.id}`;
    vis.style.pointerEvents = "none";
    gLines.append(hit, vis);
  });

  topo.buses.forEach((b) => {
    const [x, y] = pos[b.id];
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", x); c.setAttribute("cy", y);
    c.setAttribute("r", b.slack ? 6 : b.gen_mw > 0 ? 4.5 : 3);
    c.setAttribute("class", "bus");
    c.id = `bus-${b.id}`;
    c.setAttribute("fill", b.gen_mw > 0 ? "#7fb3f0" : "#4b525f");
    if (b.protected) { c.setAttribute("stroke", "#e0c060"); c.setAttribute("stroke-width", 1.6); }
    const t = document.createElementNS(SVG_NS, "title");
    t.textContent = `bus ${b.id} — load ${b.load_mw} MW, generation ${b.gen_mw} MW` +
      (b.protected ? " (protected: cannot be shed)" : "") + (b.slack ? " (slack)" : "");
    c.appendChild(t);
    gBuses.appendChild(c);
  });
}

function titleFor(id, pct) {
  const t = document.createElementNS(SVG_NS, "title");
  t.textContent = pct === null ? `line ${id} — click to trip`
    : `line ${id} — ${pct}% loading (click to trip)`;
  return t;
}

function render(s) {
  if (!topo) return;
  s.lines.forEach((ln) => {
    const vis = document.getElementById(`line-${ln.id}`);
    if (!vis) return;
    if (!ln.in_service) {
      vis.setAttribute("stroke", "#5b6270");
      vis.setAttribute("stroke-width", 1.4);
      vis.setAttribute("stroke-dasharray", "4 4");
      vis.classList.remove("hot");
    } else {
      vis.setAttribute("stroke", loadingColor(ln.loading));
      vis.setAttribute("stroke-width", ln.loading >= 100 ? 4 : ln.loading >= 90 ? 3 : 2);
      vis.removeAttribute("stroke-dasharray");
      vis.classList.toggle("hot", ln.loading >= 100);
    }
    const hit = vis.previousSibling;
    if (hit) { hit.textContent = ""; hit.appendChild(titleFor(ln.id, ln.loading)); }
  });

  s.buses.forEach((b) => {
    const c = document.getElementById(`bus-${b.id}`);
    if (!c) return;
    if (!b.energized) { c.setAttribute("fill", "#6b7280"); c.setAttribute("opacity", .45); }
    else { c.removeAttribute("opacity"); }
  });

  const m = s.metrics;
  el("m-served").textContent = `${m.load_served_mw.toLocaleString()} MW`;
  el("m-lost").textContent = `${m.load_lost_mw.toLocaleString()} MW`;
  el("m-out").textContent = m.lines_tripped;
  el("m-over").textContent = m.overloaded_lines;
  el("m-islands").textContent = m.islands;
  el("m-forecast").textContent = s.forecast
    ? `${s.forecast.would_lose_mw.toLocaleString()} MW lost`
    : "—";

  const box = el("events");
  box.textContent = "";
  (s.log || []).slice().reverse().forEach((e) => {
    const d = document.createElement("div");
    d.className = `ev ${e.kind}`;
    d.innerHTML = `<b>t${e.tick} ${e.kind}</b> — ${escapeHtml(e.text)}`;
    box.appendChild(d);
  });
  (s.events || []).slice().reverse().forEach((e) => {
    const d = document.createElement("div");
    d.className = "ev";
    const mw = e.mw ? ` (${e.mw.toFixed(1)} MW)` : "";
    d.innerHTML = `<b>t${e.tick} ${e.kind}</b> — ${escapeHtml(e.detail)}${mw}`;
    box.appendChild(d);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function addTurn(t) {
  const box = el("transcript");
  const hint = box.querySelector(".hint");
  if (hint) hint.remove();
  const d = document.createElement("div");
  const rejected = /^REJECTED/.test(t.result);
  const applied = t.tool === "apply_actions" && !rejected;
  d.className = `turn ${applied ? "applied" : rejected ? "rejected" : ""}`;
  d.innerHTML =
    `<div class="tool">t${t.tick} · ${escapeHtml(t.tool || "unparseable")}</div>` +
    `<div class="args">${escapeHtml(JSON.stringify(t.args).slice(0, 220))}</div>` +
    `<div class="res">${escapeHtml(t.result.slice(0, 320))}</div>`;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
}

function setAgentState(p) {
  const b = el("agent-state");
  b.textContent = p.state;
  b.className = `badge ${p.state === "thinking" ? "thinking" : "idle"}`;
  const busy = p.state === "thinking";
  ["btn-agent", "btn-step", "btn-settle", "btn-heuristic"].forEach((id) => {
    el(id).disabled = busy;
  });
  if (p.stats) {
    const d = document.createElement("div");
    d.className = "turn";
    d.innerHTML = `<div class="res">${p.stats.turns} turns · ` +
      `${p.stats.applied} actions applied · ${p.stats.invalid_actions} invalid · ` +
      `${p.stats.guardrail_rejections} blocked by guardrail</div>`;
    el("transcript").appendChild(d);
  }
}

function announce(sc) {
  const d = document.createElement("div");
  d.className = "turn";
  d.innerHTML = `<div class="tool">${escapeHtml(sc.id)} · ${escapeHtml(sc.kind)}</div>` +
                `<div class="res">${escapeHtml(sc.description)}</div>`;
  el("transcript").appendChild(d);
}

el("btn-step").onclick = () => send({ cmd: "step", ticks: 1 });
el("btn-settle").onclick = () => send({ cmd: "settle" });
el("btn-agent").onclick = () => send({ cmd: "agent" });
el("btn-heuristic").onclick = () => send({ cmd: "heuristic" });
el("btn-reset").onclick = () => {
  el("transcript").textContent = "";
  send({ cmd: "reset" });
};
el("scenario").onchange = (e) => {
  if (e.target.value !== "") {
    el("transcript").textContent = "";
    send({ cmd: "scenario", seed: Number(e.target.value) });
  }
};

fetch("/api/scenarios").then((r) => r.json()).then((d) => {
  const sel = el("scenario");
  const add = (rows, label) => {
    if (!rows || !rows.length) return;
    const g = document.createElement("optgroup");
    g.label = label;
    rows.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.seed;
      const lost = r.no_action_load_lost_mw;
      o.textContent = `${r.id} ${r.kind} — ${lost > 0 ? `${lost} MW lost if ignored` : "absorbed"}`;
      g.appendChild(o);
    });
    sel.appendChild(g);
  };
  add(d.damaging, "damaging incidents");
  add(d.benign, "incidents the grid absorbs");
}).catch(() => {});

connect();

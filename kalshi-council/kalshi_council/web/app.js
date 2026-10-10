// Dashboard wiring: initial state, live SSE events, panels, and the agent drawer.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  const cents = (v) => (v == null ? "—" : `${(v * 100).toFixed(v < 0.01 || v > 0.99 ? 1 : 0)}¢`);
  const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

  let agents = {};
  let state = null;
  let refreshTimer = null;

  // ---- tabs -----------------------------------------------------------------
  document.querySelectorAll(".side a").forEach((a) => a.addEventListener("click", () => {
    document.querySelectorAll(".side a").forEach((x) => x.classList.toggle("active", x === a));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + a.dataset.tab));
    if (a.dataset.tab === "hub") window.dispatchEvent(new Event("resize"));
  }));

  // ---- stats ----------------------------------------------------------------
  function renderStats(s) {
    const pnl = $("#s-pnl");
    pnl.textContent = `${s.session_pnl >= 0 ? "+" : ""}${money(s.session_pnl)}`;
    pnl.classList.toggle("neg", s.session_pnl < 0);
    $("#s-total").textContent = `all-time ${money(s.total_pnl)} · today ${money(s.today_pnl)}`;
    $("#s-win").textContent = pct(s.win_rate);
    $("#s-settled").textContent = s.settled.toLocaleString();
    $("#s-today").textContent = `${s.settled_today} today`;
    $("#s-signals").textContent = s.signals_scanned.toLocaleString();
    $("#s-open").textContent = s.open_positions;
    $("#s-risk").textContent = `${money(s.at_risk)} at risk`;
    const mode = $("#mode");
    mode.textContent = `${s.mode.toUpperCase()} · ${s.exchange}`;
    mode.classList.toggle("live", s.mode === "live");
    $("#balances").innerHTML = Object.entries(s.buckets).map(([k, v]) => `<span>${esc(k)} <b>${money(v)}</b></span>`).join("")
      + `<span class="total">equity <b>${money(s.equity)}</b></span>`;
    const btn = $("#pause");
    btn.textContent = s.paused ? "Resume trading" : "Pause trading";
    btn.classList.toggle("paused", s.paused);
    btn.dataset.paused = s.paused ? "1" : "";
  }

  // ---- feed -----------------------------------------------------------------
  function who(ev) {
    if (ev.agent) { const a = agents[ev.agent]; return `<span class="who agentlink" data-agent="${esc(ev.agent)}" style="color:${esc(a?.color || "#c9c3ff")}">${esc(a?.name || ev.agent)}</span>`; }
    if (ev.bot) return `<span class="who" style="color:#36e2c8">${esc(botName(ev.bot))}</span>`;
    return `<span class="who" style="color:#9a96d1">${esc(ev.kind)}</span>`;
  }
  function botName(id) { return (state?.bots || []).find((b) => b.id === id)?.name || (id === "council" ? "Council" : id); }
  function feedItem(ev) {
    if (!ev.text) return;
    const li = document.createElement("li");
    const cls = ev.kind === "settlement" ? (ev.won ? "win" : "loss") : ev.kind === "error" ? "err" : "";
    li.innerHTML = `<time>${hhmmss(ev.ts)}</time>${who(ev)}<span class="txt ${cls}">${esc(ev.text)}</span>`;
    const feed = $("#feed");
    feed.prepend(li);
    while (feed.children.length > 150) feed.lastChild.remove();
  }

  // ---- council ----------------------------------------------------------------
  function renderVerdicts(list) {
    const el = $("#verdicts");
    if (!list.length) { el.innerHTML = `<p class="muted">No live forecasts yet. Connect an MCP agent or start the built-in crew with <code>--crew</code>.</p>`; return; }
    el.innerHTML = list.slice().reverse().map((v) => `
      <div class="verdict">
        <div class="top"><div><div class="t">${esc(v.title)}</div><div class="reason">${esc(v.ticker)} · YES ask ${cents(v.yes_ask)} · NO ask ${cents(v.no_ask)}</div></div>
        <span class="dec ${esc(v.decision)}">${esc(v.decision.toUpperCase())}</span></div>
        <div class="chips">${v.votes.map((x) => `<span class="chip ${esc(x.vote)} agentlink" data-agent="${esc(x.agent)}" title="${esc(x.rationale)}">${esc(agents[x.agent]?.name || x.agent)} · fair ${cents(x.fair)} · ${esc(x.vote)}</span>`).join("")}</div>
        <div class="reason">consensus ${cents(v.consensus)} · ${esc(v.reason)}</div>
      </div>`).join("");
  }

  function renderBoard(rows) {
    $("#board tbody").innerHTML = rows.map((r) => `
      <tr><td><span class="dot" style="background:${esc(r.color || "#888")}"></span><span class="agentlink" data-agent="${esc(r.agent)}">${esc(r.name)}</span></td>
      <td>${r.forecasts}</td><td>${r.scored}</td>
      <td class="${r.skill > 0 ? "pos" : r.skill < 0 ? "neg" : ""}">${r.scored ? (r.skill >= 0 ? "+" : "") + r.skill.toFixed(4) : "—"}</td>
      <td>${r.hit_rate == null ? "—" : pct(r.hit_rate)}</td><td>${r.weight.toFixed(2)}</td></tr>`).join("");
  }

  // ---- signals & debates ------------------------------------------------------
  function renderSignals(list) {
    $("#signals tbody").innerHTML = list.slice().reverse().slice(0, 60).map((s) => `
      <tr><td>${esc(botName(s.strategy))}</td><td>${esc(s.text)}</td><td class="${s.edge > 0 ? "pos" : "neg"}">${(s.edge * 100).toFixed(1)}¢</td></tr>`).join("");
  }
  const debates = [];
  function addDebate(ev) {
    if (!ev.stances) return;
    debates.unshift(ev); debates.length = Math.min(debates.length, 25);
    $("#debates").innerHTML = debates.map((d) => `
      <div class="debate ${d.approved ? "ok" : "no"}"><div><b>${esc(botName(d.bot))}</b> · ${esc(d.ticker)} — ${esc(d.text)}</div>
      ${d.stances.map((s) => `<div class="s"><span class="e">${esc(s.emotion)}</span><span class="${s.stance > 0 ? "p" : s.stance < 0 ? "m" : ""}">${esc(s.line)}</span></div>`).join("")}</div>`).join("");
  }

  // ---- positions ------------------------------------------------------------
  async function renderPositions() {
    const p = await (await fetch("/api/positions")).json();
    $("#open tbody").innerHTML = p.open.map((x) => `<tr><td>${esc(x.title)}<div class="reason">${esc(x.ticker)}</div></td><td>${esc(x.side.toUpperCase())}</td><td>${x.count}</td><td>${cents(x.avg_price)}</td><td>${money(x.cost)}</td><td>${esc(x.bucket)}</td><td>${esc(x.strategies.join(", "))}</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">No open positions.</td></tr>`;
    $("#closed tbody").innerHTML = p.closed.map((x) => `<tr><td>${esc(x.title)}</td><td>${esc(x.side.toUpperCase())}</td><td>${x.count}</td><td>${cents(x.price)}</td><td>${esc((x.result || "").toUpperCase())}</td><td class="${x.pnl >= 0 ? "pos" : "neg"}">${money(x.pnl)}</td><td>${esc(x.strategy)}</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">Nothing settled yet.</td></tr>`;
  }

  // ---- full refresh -----------------------------------------------------------
  async function refresh() {
    state = await (await fetch("/api/state")).json();
    agents = Object.fromEntries(state.agents.map((a) => [a.agent, a]));
    renderStats(state.stats);
    renderVerdicts(state.verdicts);
    renderBoard(state.agents);
    renderSignals(state.signals);
    Ship.setAgents(state.agents);
    Ship.setBots(state.bots);
    const s = state.stats;
    const forecasts = state.agents.reduce((n, a) => n + a.forecasts, 0);
    const wins = Math.round((s.win_rate || 0) * s.settled);
    Ship.setRoomInfo({
      research: `${s.signals_scanned.toLocaleString()} signals`, council: `${forecasts} forecasts`,
      trading: `${s.open_positions} open`, vault: `${wins}W / ${s.settled - wins}L`,
      bridge: `${state.agents.length} aboard`, engine: `${state.bots.length} bots`,
    });
    if ($("#tab-positions").classList.contains("active")) renderPositions();
  }
  function scheduleRefresh() { if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = null; refresh(); }, 1500); }

  // ---- live events ------------------------------------------------------------
  function connect() {
    const es = new EventSource("/api/events");
    const st = $("#status");
    es.onopen = () => { st.classList.add("on"); st.querySelector("b").textContent = "engine online"; };
    es.onerror = () => { st.classList.remove("on"); st.querySelector("b").textContent = "reconnecting"; };
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.kind === "stats") { renderStats(ev); return; }
      Ship.handleEvent(ev);
      feedItem(ev);
      if (ev.kind === "debate") addDebate(ev);
      if (["council", "forecast", "settlement", "order", "agent", "signal", "scored"].includes(ev.kind)) scheduleRefresh();
    };
  }

  // ---- agent drawer -------------------------------------------------------------
  async function openAgent(id) {
    const r = await fetch(`/api/agents/${encodeURIComponent(id)}`);
    if (!r.ok) return;
    const d = await r.json();
    const a = d.agent, sc = d.score || {};
    const thought = d.activity.filter((e) => e.kind === "thought").pop();
    const lastRationale = d.forecasts[0]?.rationale;
    $("#drawer-body").innerHTML = `
      <h2><span class="dot" style="background:${esc(a.color || "#888")}"></span>${esc(a.name)} <span class="muted">· ${esc(a.model || a.source)}</span></h2>
      <p class="persona">${esc(a.persona || "One of your AI agents. Every one of them shows up on this ship as it happens.")}</p>
      <h3>What it's thinking</h3>
      <div class="thought">${esc(thought?.text || lastRationale || `${a.name} hasn't said anything yet. It talks through its tool calls; watch the ship.`)}</div>
      <h3>Track record</h3>
      <p class="muted">${sc.forecasts || 0} forecasts · ${sc.scored || 0} scored · skill ${sc.scored ? (sc.skill >= 0 ? "+" : "") + sc.skill.toFixed(4) : "—"} · council weight ${(sc.weight ?? 1).toFixed(2)}</p>
      <h3>Recent forecasts</h3>
      <table class="table"><tbody>${d.forecasts.map((f) => `<tr><td>${esc(f.title)}<div class="reason">${esc(f.rationale)}</div></td><td>${cents(f.fair)}</td><td>${f.outcome == null ? "open" : f.outcome ? "YES" : "NO"}</td></tr>`).join("") || `<tr><td class="muted">None yet.</td></tr>`}</tbody></table>
      <h3>Activity</h3>
      <ul class="feed">${d.activity.slice().reverse().map((e) => `<li><time>${hhmmss(e.ts)}</time><span class="txt">${esc(e.text)}</span></li>`).join("")}</ul>`;
    $("#drawer").classList.remove("hidden");
  }
  $("#drawer-close").addEventListener("click", () => $("#drawer").classList.add("hidden"));
  $("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); });
  document.addEventListener("click", (e) => { const el = e.target.closest("[data-agent]"); if (el) openAgent(el.dataset.agent); });

  // ---- controls ---------------------------------------------------------------
  $("#pause").addEventListener("click", async (e) => {
    const action = e.currentTarget.dataset.paused ? "resume" : "pause";
    await fetch("/api/control", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
    refresh();
  });
  document.querySelector('[data-tab="positions"]').addEventListener("click", renderPositions);

  $("#btform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const out = $("#btout");
    out.innerHTML = `<p class="muted">Replaying ${esc(f.get("markets"))} settled ${esc(f.get("asset"))} markets…</p>`;
    const r = await fetch("/api/backtest", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ asset: f.get("asset"), markets: Number(f.get("markets")) }) });
    if (!r.ok) { out.innerHTML = `<p class="neg">${esc(await r.text())}</p>`; return; }
    const d = await r.json();
    const c = d.calibration;
    out.innerHTML = `
      <p><b>${d.configs_with_edge}/${d.configs_tested}</b> settings show edge on ${d.markets_used} markets.
      Model Brier ${c.model_brier ?? "—"} vs market ${c.market_brier ?? "—"} at ${c.entry_seconds_left}s left (lower is better).</p>
      <table class="table"><thead><tr><th>Entry</th><th>Min edge</th><th>Vol ×</th><th>Trades</th><th>Win</th><th>P&amp;L/contract</th><th>t</th><th>Edge?</th></tr></thead>
      <tbody>${d.best.map((g) => `<tr><td>${g.entry_seconds_left}s</td><td>${cents(g.min_edge)}</td><td>${g.vol_scale}</td><td>${g.trades}</td><td>${pct(g.win_rate)}</td>
        <td class="${g.pnl_per_contract >= 0 ? "pos" : "neg"}">${(g.pnl_per_contract * 100).toFixed(1)}¢</td><td>${g.t_stat ?? "—"}</td><td>${g.has_edge ? "yes" : "no"}</td></tr>`).join("")}</tbody></table>
      <p class="muted">${esc(d.caveats)}</p>`;
  });

  Ship.init($("#ship"), { onAgentClick: openAgent });
  refresh().then(() => (state.events || []).forEach(feedItem));
  connect();
  setInterval(refresh, 15000);
})();

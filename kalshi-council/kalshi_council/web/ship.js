// The Agent Hub: an isometric ship where every crew member walks to the room of each tool call.
(() => {
  const TW = 64, TH = 32, WALL = 46;
  const ROOMS = {
    bridge:   { x: 0, y: 0, w: 4, h: 4, name: "Bridge", color: "#5b8cff" },
    research: { x: 4, y: 0, w: 5, h: 4, name: "Research Lab", color: "#36e2c8" },
    council:  { x: 9, y: 0, w: 4, h: 4, name: "The Council", color: "#ffb347" },
    forge:    { x: 0, y: 4, w: 4, h: 4, name: "Strategy Forge", color: "#ff7a59" },
    trading:  { x: 4, y: 4, w: 5, h: 4, name: "Trading Desk", color: "#ff4fa3" },
    vault:    { x: 9, y: 4, w: 4, h: 4, name: "Win/Loss Vault", color: "#3ee08f" },
    backtest: { x: 0, y: 8, w: 7, h: 3, name: "Backtest Chamber", color: "#7c9cff" },
    engine:   { x: 7, y: 8, w: 6, h: 3, name: "Engine Room", color: "#b38cff" },
  };
  const EMOTIONS = [
    ["Greed", "#3ee08f"], ["Fear", "#8aa1ff"], ["FOMO", "#ff4fa3"],
    ["Doubt", "#c9c3ff"], ["Patience", "#36e2c8"], ["Discipline", "#ffc857"],
  ];
  const ACCESSORIES = ["antenna", "halo", "cap", "sprout", "horns", "visor"];

  let canvas, ctx, dpr = 1, W = 0, H = 0;
  let scale = 1, ox = 0, oy = 0, panX = 0, panY = 0, zoom = 1;
  const sprites = new Map();
  const floaters = [];
  const confetti = [];
  let payday = 0, paydayText = "";
  let roomInfo = {};
  let onAgentClick = () => {};
  const emotionState = EMOTIONS.map(() => ({ stance: 0, until: 0 }));

  const now = () => performance.now() / 1000;
  const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };

  function shade(hex, amt) {
    const n = parseInt(hex.replace("#", ""), 16);
    const f = (v) => Math.max(0, Math.min(255, Math.round(v + amt * 255)));
    return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
  }
  function alpha(hex, a) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // world tile coords -> screen pixels
  function iso(x, y, z = 0) {
    return [ox + panX + (x - y) * (TW / 2) * scale, oy + panY + ((x + y) * (TH / 2) - z) * scale];
  }

  function fit() {
    const r = canvas.getBoundingClientRect();
    if (r.width < 50 || r.height < 50) return false; // hidden tab: keep the last good layout
    dpr = window.devicePixelRatio || 1;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const spanX = (13 + 11) * TW / 2, spanY = (13 + 11) * TH / 2 + WALL;
    scale = Math.min((W - 40) / spanX, (H - 40) / spanY) * zoom;
    ox = W / 2 - ((13 - 11) * TW / 4) * scale;
    oy = (H - spanY * scale) / 2 + WALL * scale;
    return true;
  }

  function poly(pts, fill, stroke) {
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }

  function box(x, y, w, d, h, color) {
    const top = [iso(x, y, h), iso(x + w, y, h), iso(x + w, y + d, h), iso(x, y + d, h)];
    const left = [iso(x, y + d, h), iso(x + w, y + d, h), iso(x + w, y + d, 0), iso(x, y + d, 0)];
    const right = [iso(x + w, y, h), iso(x + w, y + d, h), iso(x + w, y + d, 0), iso(x + w, y, 0)];
    poly(left, shade(color, -0.18));
    poly(right, shade(color, -0.3));
    poly(top, shade(color, 0.05));
  }

  function disc(x, y, z, rx, color, glow) {
    const [cx, cy] = iso(x, y, z);
    ctx.save();
    if (glow) { ctx.shadowColor = color; ctx.shadowBlur = 18 * scale; }
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx * TW / 2 * scale, rx * TH / 2 * scale, 0, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.restore();
  }

  function drawRoomFloor(key, r, t) {
    const flash = Math.max(0, 1 - (t - (r.flash || -9)) / 1.6);
    const pts = [iso(r.x, r.y), iso(r.x + r.w, r.y), iso(r.x + r.w, r.y + r.h), iso(r.x, r.y + r.h)];
    poly(pts, alpha("#d9d4ff", 0.16 + flash * 0.25), alpha(r.color, 0.5 + flash * 0.5));
    ctx.save();
    ctx.globalAlpha = 0.12;
    ctx.strokeStyle = "#ffffff";
    for (let i = 1; i < r.w; i++) { ctx.beginPath(); ctx.moveTo(...iso(r.x + i, r.y)); ctx.lineTo(...iso(r.x + i, r.y + r.h)); ctx.stroke(); }
    for (let j = 1; j < r.h; j++) { ctx.beginPath(); ctx.moveTo(...iso(r.x, r.y + j)); ctx.lineTo(...iso(r.x + r.w, r.y + j)); ctx.stroke(); }
    ctx.restore();
    // back walls (glass)
    const wallA = [iso(r.x, r.y, 0), iso(r.x + r.w, r.y, 0), iso(r.x + r.w, r.y, WALL * 0.6), iso(r.x, r.y, WALL * 0.6)];
    const wallB = [iso(r.x, r.y, 0), iso(r.x, r.y + r.h, 0), iso(r.x, r.y + r.h, WALL * 0.6), iso(r.x, r.y, WALL * 0.6)];
    poly(wallA, alpha(r.color, 0.1 + flash * 0.15), alpha(r.color, 0.35));
    poly(wallB, alpha(r.color, 0.07 + flash * 0.12), alpha(r.color, 0.3));
  }

  function furniture(t) {
    const items = [];
    const add = (x, y, fn) => items.push({ depth: x + y, draw: fn });
    // bridge: holo table + console
    add(1.8, 1.8, () => { disc(2, 2, 2, 1.0, "#3a4fd0"); disc(2, 2, 10, 0.75, alpha("#5b8cff", 0.35 + 0.15 * Math.sin(t * 2)), true); });
    add(0.4, 0.6, () => box(0.3, 0.4, 2.2, 0.5, 14, "#4652a8"));
    // research desks with screens
    for (let i = 0; i < 3; i++) add(4.6 + i * 1.5, 0.8, () => { box(4.5 + i * 1.5, 0.5, 1.1, 0.6, 12, "#2f6f86"); box(4.6 + i * 1.5, 0.5, 0.9, 0.1, 30, "#36e2c8"); });
    // council round table
    add(11, 2, () => { disc(11, 2, 0, 1.45, "#5a3a17"); disc(11, 2, 12, 1.3, "#c98a2c"); disc(11, 2, 13, 0.9, alpha("#ffde8a", 0.4 + 0.2 * Math.sin(t * 3)), true); });
    // forge: furnace
    add(1.2, 5, () => { box(0.6, 4.5, 1.3, 1.1, 26, "#6b3a2e"); disc(1.25, 5.6, 12, 0.35, alpha("#ff7a59", 0.6 + 0.3 * Math.sin(t * 6)), true); });
    // trading desks
    for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) add(4.8 + i * 2.2, 5 + j * 1.6, () => { box(4.6 + i * 2.2, 4.8 + j * 1.6, 1.4, 0.7, 12, "#7a2d5c"); box(4.7 + i * 2.2, 4.8 + j * 1.6, 1.2, 0.1, 28, "#ff4fa3"); });
    // vault safe
    add(10.8, 5.6, () => { box(10.2, 4.6, 2.2, 1.6, 40, "#1f6a48"); disc(11.3, 6.2, 20, 0.35, "#3ee08f", true); });
    // backtest pods
    for (let i = 0; i < 4; i++) add(1 + i * 1.6, 9.5, () => { disc(1 + i * 1.6, 9.5, 0, 0.55, "#2b3a8c"); disc(1 + i * 1.6, 9.5, 26, 0.5, alpha("#7c9cff", 0.45 + 0.25 * Math.sin(t * 2 + i)), true); });
    // engine room racks
    for (let i = 0; i < 3; i++) add(8 + i * 1.6, 8.4, () => box(7.8 + i * 1.6, 8.2, 1.0, 0.5, 34, "#40306e"));
    return items;
  }

  // ---- sprites ---------------------------------------------------------------

  function randomIn(roomKey) {
    const r = ROOMS[roomKey] || ROOMS.bridge;
    return [r.x + 0.7 + Math.random() * (r.w - 1.4), r.y + 0.9 + Math.random() * (r.h - 1.5)];
  }

  function ensure(id, opts = {}) {
    let s = sprites.get(id);
    if (!s) {
      const room = opts.kind === "bot" ? "engine" : "bridge";
      const [x, y] = randomIn(room);
      s = { id, name: opts.name || id, color: opts.color || `hsl(${hash(id) % 360} 70% 62%)`, kind: opts.kind || "agent",
            model: opts.model || "", x, y, tx: x, ty: y, room, bubble: null, lastAct: now(), home: room,
            acc: ACCESSORIES[hash(id) % ACCESSORIES.length], phase: Math.random() * 6 };
      sprites.set(id, s);
    }
    if (opts.name) s.name = opts.name;
    if (opts.color) s.color = opts.color;
    if (opts.model) s.model = opts.model;
    return s;
  }

  function goTo(s, room) {
    if (!ROOMS[room]) return;
    if (s.room !== room || Math.random() < 0.3) [s.tx, s.ty] = randomIn(room);
    s.room = room; s.lastAct = now();
  }

  function say(s, text, color) { s.bubble = { text: String(text || ""), until: now() + 6, color }; }

  function roomSay(room, text, color) { const r = ROOMS[room]; if (r) { r.bubble = { text, until: now() + 6, color }; r.flash = now(); } }

  function update(dt, t) {
    for (const s of sprites.values()) {
      const dx = s.tx - s.x, dy = s.ty - s.y, d = Math.hypot(dx, dy);
      const speed = s.kind === "bot" ? 1.8 : 2.4;
      s.moving = d > 0.05;
      if (s.moving) { const k = Math.min(1, speed * dt / d); s.x += dx * k; s.y += dy * k; s.dir = [dx / d, dy / d]; }
      const idle = t - s.lastAct;
      if (!s.moving && idle > 4 && Math.random() < dt * 0.25) [s.tx, s.ty] = randomIn(s.room);
      if (idle > 90 && s.room !== s.home) { s.room = s.home; [s.tx, s.ty] = randomIn(s.home); }
    }
    for (let i = floaters.length - 1; i >= 0; i--) if (t > floaters[i].until) floaters.splice(i, 1);
    for (let i = confetti.length - 1; i >= 0; i--) {
      const c = confetti[i]; c.vy += 260 * dt; c.x += c.vx * dt; c.y += c.vy * dt; c.r += c.vr * dt;
      if (c.y > H + 20) confetti.splice(i, 1);
    }
  }

  function drawAgent(s, t) {
    const bob = s.moving ? Math.abs(Math.sin(t * 10 + s.phase)) * 3 : Math.sin(t * 2 + s.phase) * 1.2;
    const [cx, cy0] = iso(s.x, s.y);
    const k = scale * 1.05;
    const cy = cy0 - bob * k;
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,.35)";
    ctx.beginPath(); ctx.ellipse(cx, cy0, 11 * k, 5 * k, 0, 0, Math.PI * 2); ctx.fill();
    // feet
    const step = s.moving ? Math.sin(t * 14 + s.phase) * 3 * k : 0;
    ctx.fillStyle = shade(toHex(s.color), -0.3);
    ctx.beginPath(); ctx.ellipse(cx - 5 * k, cy0 - 2 * k + step * 0.3, 4 * k, 2.6 * k, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(cx + 5 * k, cy0 - 2 * k - step * 0.3, 4 * k, 2.6 * k, 0, 0, Math.PI * 2); ctx.fill();
    // body
    const g = ctx.createRadialGradient(cx - 4 * k, cy - 22 * k, 2 * k, cx, cy - 14 * k, 18 * k);
    g.addColorStop(0, shade(toHex(s.color), 0.25)); g.addColorStop(1, s.color);
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.ellipse(cx, cy - 14 * k, 12 * k, 14 * k, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,.18)";
    ctx.beginPath(); ctx.ellipse(cx, cy - 9 * k, 7 * k, 6 * k, 0, 0, Math.PI * 2); ctx.fill();
    // eyes, looking where it walks
    const [lx, ly] = s.dir ? [s.dir[0] - s.dir[1], (s.dir[0] + s.dir[1]) * 0.5] : [0, 0.4];
    const blink = (Math.sin(t * 0.9 + s.phase * 3) > 0.985) ? 0.15 : 1;
    for (const ex of [-4.5, 4.5]) {
      ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.ellipse(cx + ex * k, cy - 18 * k, 3.6 * k, 4.4 * k * blink, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#1b1640";
      ctx.beginPath(); ctx.arc(cx + ex * k + lx * 1.3 * k, cy - 18 * k + ly * 1.3 * k, 1.8 * k * blink, 0, Math.PI * 2); ctx.fill();
    }
    accessory(s, cx, cy, k, t);
    ctx.restore();
    label(cx, cy0 + 12 * k, s.name, s.color);
  }

  function accessory(s, cx, cy, k, t) {
    const top = cy - 28 * k;
    ctx.strokeStyle = shade(toHex(s.color), -0.35); ctx.lineWidth = 1.5 * k;
    switch (s.acc) {
      case "antenna":
        ctx.beginPath(); ctx.moveTo(cx, top + 1 * k); ctx.lineTo(cx + 3 * k, top - 8 * k); ctx.stroke();
        ctx.fillStyle = "#ffe066"; ctx.beginPath(); ctx.arc(cx + 3 * k, top - 9 * k, 2.4 * k, 0, Math.PI * 2); ctx.fill(); break;
      case "halo":
        ctx.strokeStyle = `rgba(255,236,150,${0.7 + 0.3 * Math.sin(t * 3)})`; ctx.lineWidth = 2 * k;
        ctx.beginPath(); ctx.ellipse(cx, top - 5 * k, 8 * k, 2.6 * k, 0, 0, Math.PI * 2); ctx.stroke(); break;
      case "cap":
        ctx.fillStyle = "#1f1a4a"; ctx.beginPath(); ctx.ellipse(cx, top + 3 * k, 9 * k, 4 * k, 0, Math.PI, 0); ctx.fill();
        ctx.fillRect(cx - 1 * k, top + 1 * k, 12 * k, 2.4 * k); break;
      case "sprout":
        ctx.strokeStyle = "#2f9e5a"; ctx.beginPath(); ctx.moveTo(cx, top + 1 * k); ctx.lineTo(cx, top - 5 * k); ctx.stroke();
        ctx.fillStyle = "#3ee08f";
        ctx.beginPath(); ctx.ellipse(cx - 3 * k, top - 6 * k, 3.4 * k, 1.8 * k, -0.5, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(cx + 3 * k, top - 6 * k, 3.4 * k, 1.8 * k, 0.5, 0, Math.PI * 2); ctx.fill(); break;
      case "horns":
        ctx.fillStyle = "#f4e9d0";
        for (const sx of [-1, 1]) { ctx.beginPath(); ctx.moveTo(cx + sx * 5 * k, top + 3 * k); ctx.lineTo(cx + sx * 9 * k, top - 5 * k); ctx.lineTo(cx + sx * 8 * k, top + 4 * k); ctx.fill(); }
        break;
      default: // visor
        ctx.fillStyle = "rgba(20,16,60,.75)"; ctx.fillRect(cx - 9 * k, cy - 21 * k, 18 * k, 6 * k);
        ctx.fillStyle = "#36e2c8"; ctx.fillRect(cx - 7 * k + ((t * 14) % 12) * k, cy - 19 * k, 3 * k, 2 * k);
    }
  }

  function drawBot(s, t) {
    const [cx, cy0] = iso(s.x, s.y);
    const k = scale * 0.95;
    const hover = Math.sin(t * 3 + s.phase) * 2 * k;
    const cy = cy0 - 6 * k + hover;
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,.3)"; ctx.beginPath(); ctx.ellipse(cx, cy0, 9 * k, 4 * k, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#c9cdf5";
    roundRect(cx - 10 * k, cy - 22 * k, 20 * k, 18 * k, 5 * k); ctx.fill();
    ctx.fillStyle = "#23205a"; roundRect(cx - 8 * k, cy - 19 * k, 16 * k, 7 * k, 3 * k); ctx.fill();
    ctx.fillStyle = s.color; ctx.fillRect(cx - 6 * k, cy - 17 * k, 4 * k, 3 * k); ctx.fillRect(cx + 2 * k, cy - 17 * k, 4 * k, 3 * k);
    ctx.strokeStyle = "#9aa0d8"; ctx.lineWidth = 1.5 * k;
    ctx.beginPath(); ctx.moveTo(cx, cy - 22 * k); ctx.lineTo(cx, cy - 28 * k); ctx.stroke();
    ctx.fillStyle = (Math.sin(t * 4 + s.phase) > 0) ? s.color : "#555"; ctx.beginPath(); ctx.arc(cx, cy - 29 * k, 2 * k, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    label(cx, cy0 + 10 * k, s.name, "#c9cdf5");
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function toHex(c) {
    if (c.startsWith("#")) return c;
    const el = document.createElement("canvas").getContext("2d"); el.fillStyle = c; return el.fillStyle;
  }

  function label(x, y, text, color) {
    ctx.save();
    ctx.font = `600 ${Math.max(9, 10.5 * scale)}px ui-monospace, Menlo, monospace`;
    const w = ctx.measureText(text).width + 10;
    ctx.fillStyle = "rgba(12,10,40,.8)"; roundRect(x - w / 2, y - 7 * scale, w, 14 * scale, 7 * scale); ctx.fill();
    ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(text, x, y);
    ctx.restore();
  }

  function wrap(text, maxW, maxLines) {
    const words = text.split(/\s+/); const lines = []; let line = "";
    for (const w of words) {
      const test = line ? line + " " + w : w;
      if (ctx.measureText(test).width > maxW && line) { lines.push(line); line = w; } else line = test;
      if (lines.length === maxLines) break;
    }
    if (lines.length < maxLines && line) lines.push(line);
    if (lines.length === maxLines && words.join(" ").length > lines.join(" ").length + 1) lines[maxLines - 1] = lines[maxLines - 1].replace(/.{0,2}$/, "…");
    return lines;
  }

  function bubble(x, y, b, t) {
    if (!b || t > b.until) return;
    const a = Math.min(1, (b.until - t) * 1.5);
    ctx.save();
    ctx.globalAlpha = a;
    ctx.font = `500 ${Math.max(9.5, 11 * scale)}px ui-monospace, Menlo, monospace`;
    const lines = wrap(b.text, 210 * Math.max(0.8, scale), 3);
    const lh = 14 * Math.max(0.85, scale);
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
    const h = lines.length * lh + 10;
    const bx = Math.min(Math.max(x - w / 2, 4), W - w - 4), by = y - h - 10;
    ctx.fillStyle = "rgba(250,248,255,.95)"; roundRect(bx, by, w, h, 8); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x - 5, by + h); ctx.lineTo(x + 5, by + h); ctx.lineTo(x, by + h + 7); ctx.fill();
    if (b.color) { ctx.fillStyle = b.color; ctx.fillRect(bx, by + 4, 3, h - 8); }
    ctx.fillStyle = "#1b1640"; ctx.textBaseline = "top";
    lines.forEach((l, i) => ctx.fillText(l, bx + 8, by + 5 + i * lh));
    ctx.restore();
  }

  function drawRoomLabels(t) {
    for (const [key, r] of Object.entries(ROOMS)) {
      const [x, y] = iso(r.x + r.w * 0.5, r.y + r.h * 0.35, WALL * 0.9);
      ctx.save();
      ctx.font = `700 ${Math.max(9.5, 11 * scale)}px ui-monospace, Menlo, monospace`;
      const info = roomInfo[key] ? `  ${roomInfo[key]}` : "";
      const text = r.name.toUpperCase();
      const w = ctx.measureText(text).width + ctx.measureText(info).width + 20;
      ctx.fillStyle = "rgba(14,11,46,.88)"; ctx.strokeStyle = alpha(r.color, 0.9); ctx.lineWidth = 1.5;
      roundRect(x - w / 2, y - 10, w, 20, 10); ctx.fill(); ctx.stroke();
      ctx.fillStyle = r.color; ctx.textBaseline = "middle"; ctx.fillText(text, x - w / 2 + 10, y);
      ctx.fillStyle = "#a7a3d9"; ctx.font = `500 ${Math.max(9, 10 * scale)}px ui-monospace, Menlo, monospace`;
      ctx.fillText(info, x - w / 2 + 10 + ctx.measureText(text).width * 1.06, y);
      ctx.restore();
      if (r.bubble) { const [bx, by] = iso(r.x + r.w / 2, r.y + r.h / 2, 30); bubble(bx, by, r.bubble, t); }
    }
  }

  function drawEmotions(t) {
    const r = ROOMS.forge;
    EMOTIONS.forEach(([name, color], i) => {
      const ang = (i / EMOTIONS.length) * Math.PI * 2 + t * 0.4;
      const x = r.x + 2.4 + Math.cos(ang) * 1.0, y = r.y + 2.4 + Math.sin(ang) * 1.0;
      const st = emotionState[i];
      const active = t < st.until;
      const c = !active ? color : st.stance > 0 ? "#3ee08f" : st.stance < 0 ? "#ff5d7a" : "#c9c3ff";
      disc(x, y, 18 + Math.sin(t * 3 + i) * 3, active ? 0.3 : 0.22, c, true);
      if (active) {
        const [lx, ly] = iso(x, y, 34);
        ctx.save(); ctx.font = `700 ${Math.max(8, 9 * scale)}px ui-monospace, monospace`; ctx.textAlign = "center";
        ctx.fillStyle = c; ctx.fillText(`${name} ${st.stance > 0 ? "+" : st.stance < 0 ? "−" : "·"}`, lx, ly); ctx.restore();
      }
    });
  }

  function frame() {
    try { draw(); } catch (e) { console.error(e); }
    requestAnimationFrame(frame);
  }

  function draw() {
    if (canvas.clientWidth !== Math.round(W) || canvas.clientHeight !== Math.round(H)) fit();
    if (W < 50) return;
    const t = now();
    const dt = Math.min(0.05, t - (frame.last || t)); frame.last = t;
    update(dt, t);
    ctx.clearRect(0, 0, W, H);
    // stars
    ctx.fillStyle = "rgba(255,255,255,.5)";
    for (let i = 0; i < 60; i++) { const sx = (hash("s" + i) % 1000) / 1000 * W, sy = (hash("y" + i) % 1000) / 1000 * H; ctx.fillRect(sx, sy, 1.2, 1.2); }
    // hull
    const hull = [iso(-0.4, -0.4), iso(13.4, -0.4), iso(13.4, 11.4), iso(-0.4, 11.4)];
    poly(hull, "rgba(80,60,200,.18)", "rgba(124,92,255,.6)");
    const lip = [iso(-0.4, 11.4), iso(13.4, 11.4), iso(13.4, 11.4, -14), iso(-0.4, 11.4, -14)];
    const lip2 = [iso(13.4, -0.4), iso(13.4, 11.4), iso(13.4, 11.4, -14), iso(13.4, -0.4, -14)];
    poly(lip, "rgba(54,38,150,.8)"); poly(lip2, "rgba(40,28,120,.8)");
    for (const [key, r] of Object.entries(ROOMS)) drawRoomFloor(key, r, t);
    const items = furniture(t);
    for (const s of sprites.values()) items.push({ depth: s.x + s.y + 0.01, draw: () => (s.kind === "bot" ? drawBot(s, t) : drawAgent(s, t)) });
    items.sort((a, b) => a.depth - b.depth).forEach((it) => it.draw());
    drawEmotions(t);
    drawRoomLabels(t);
    for (const s of sprites.values()) { const [x, y] = iso(s.x, s.y); bubble(x, y - (s.kind === "bot" ? 34 : 46) * scale, s.bubble, t); }
    for (const f of floaters) {
      const p = 1 - (f.until - t) / 2.5;
      ctx.save(); ctx.globalAlpha = 1 - p; ctx.font = `800 ${16 * scale + 6}px ui-monospace, monospace`; ctx.textAlign = "center";
      ctx.fillStyle = f.color; ctx.shadowColor = f.color; ctx.shadowBlur = 12;
      const [x, y] = iso(f.x, f.y, 40 + p * 60); ctx.fillText(f.text, x, y); ctx.restore();
    }
    for (const c of confetti) {
      ctx.save(); ctx.translate(c.x, c.y); ctx.rotate(c.r); ctx.fillStyle = c.color; ctx.fillRect(-4, -2, 8, 4); ctx.restore();
    }
    if (t < payday) {
      const p = 1 - (payday - t) / 3.5;
      ctx.save(); ctx.globalAlpha = Math.min(1, (payday - t) * 1.5); ctx.textAlign = "center";
      ctx.font = `900 ${Math.min(96, W / 8) * (0.9 + 0.1 * Math.sin(p * 20))}px ui-sans-serif, system-ui, sans-serif`;
      ctx.fillStyle = "#ffe066"; ctx.shadowColor = "#ff9f1c"; ctx.shadowBlur = 30;
      ctx.fillText("PAYDAY!", W / 2, H * 0.45);
      ctx.font = `800 ${Math.min(30, W / 24)}px ui-monospace, monospace`; ctx.fillStyle = "#3ee08f";
      ctx.fillText(paydayText, W / 2, H * 0.45 + 40); ctx.restore();
    }
  }

  function burst() {
    const colors = ["#ffe066", "#3ee08f", "#ff4fa3", "#36e2c8", "#7c5cff", "#ff9f1c"];
    for (let i = 0; i < 160; i++) confetti.push({ x: W / 2 + (Math.random() - 0.5) * 80, y: H * 0.4, vx: (Math.random() - 0.5) * 700,
      vy: -200 - Math.random() * 400, r: Math.random() * 6, vr: (Math.random() - 0.5) * 12, color: colors[i % colors.length] });
  }

  // ---- public API ---------------------------------------------------------------

  function handleEvent(ev) {
    const t = now();
    const room = ev.room && ROOMS[ev.room] ? ev.room : null;
    if (room) ROOMS[room].flash = t;
    switch (ev.kind) {
      case "tool_call": case "forecast": {
        if (!ev.agent) break;
        const s = ensure(ev.agent); goTo(s, room || "research"); say(s, ev.text, ROOMS[room || "research"].color); break;
      }
      case "agent": { const s = ensure(ev.agent, { name: ev.name, color: ev.color, model: ev.model }); goTo(s, "bridge"); say(s, "Reporting aboard!"); break; }
      case "thought": if (ev.agent) { const s = ensure(ev.agent); say(s, ev.text, "#9a96d1"); s.lastAct = t; } break;
      case "signal": { const b = ensure("bot:" + ev.bot, { kind: "bot" }); say(b, ev.text, ROOMS.research.color); ROOMS.research.flash = t; break; }
      case "debate":
        if (ev.stances) ev.stances.forEach((s, i) => { emotionState[i] = { stance: s.stance, until: t + 5 }; });
        roomSay(ev.room || "forge", ev.text, ev.approved ? "#3ee08f" : "#ff5d7a"); break;
      case "order": {
        if (ev.bot && ev.bot !== "council") { const b = ensure("bot:" + ev.bot, { kind: "bot" }); goTo(b, "trading"); say(b, ev.text, ROOMS.trading.color);
          setTimeout(() => goTo(b, "engine"), 7000); }
        else roomSay("trading", ev.text, ROOMS.trading.color);
        break;
      }
      case "council": roomSay("council", ev.text, ev.decision === "hold" ? "#c9c3ff" : ev.decision === "yes" ? "#3ee08f" : "#ff5d7a"); break;
      case "settlement": {
        const r = ROOMS.vault;
        floaters.push({ x: r.x + 2, y: r.y + 2, text: `${ev.pnl >= 0 ? "+" : "−"}$${Math.abs(ev.pnl).toFixed(2)}`, color: ev.won ? "#3ee08f" : "#ff5d7a", until: t + 2.5 });
        roomSay("vault", ev.text, ev.won ? "#3ee08f" : "#ff5d7a");
        if (ev.payday) { payday = t + 3.5; paydayText = `+$${ev.pnl.toFixed(2)} session P&L`; burst(); }
        break;
      }
      case "scored": case "backtest": case "engine": roomSay(room || "bridge", ev.text); break;
      case "error": roomSay(room || "bridge", ev.text, "#ffc857"); break;
    }
  }

  function setAgents(list) {
    for (const a of list) ensure(a.agent || a.id, { name: a.name, color: a.color, model: a.model });
  }
  function setBots(list) { for (const b of list) ensure("bot:" + b.id, { kind: "bot", name: b.name, color: "#36e2c8" }); }
  function setRoomInfo(info) { roomInfo = info; }

  function init(el, opts = {}) {
    canvas = el; ctx = canvas.getContext("2d"); onAgentClick = opts.onAgentClick || onAgentClick;
    fit(); window.addEventListener("resize", fit);
    let drag = null;
    canvas.addEventListener("mousedown", (e) => { drag = { x: e.clientX, y: e.clientY, px: panX, py: panY, moved: false }; });
    window.addEventListener("mouseup", (e) => {
      if (drag && !drag.moved) click(e);
      drag = null;
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) { hover(e); return; }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      panX = drag.px + dx; panY = drag.py + dy;
    });
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoom = Math.max(0.6, Math.min(2.2, zoom * (e.deltaY < 0 ? 1.1 : 0.9))); fit(); }, { passive: false });
    requestAnimationFrame(frame);
  }

  function pick(e) {
    const r = canvas.getBoundingClientRect(); const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = null, bd = 26 * scale + 8;
    for (const s of sprites.values()) {
      const [x, y] = iso(s.x, s.y); const d = Math.hypot(mx - x, my - (y - 14 * scale));
      if (d < bd) { bd = d; best = s; }
    }
    return best;
  }
  function click(e) { if (e.target !== canvas) return; const s = pick(e); if (s && s.kind === "agent") onAgentClick(s.id); }
  function hover(e) { if (e.target !== canvas) return; const s = pick(e); canvas.style.cursor = s && s.kind === "agent" ? "pointer" : "grab"; }

  window.Ship = { init, handleEvent, setAgents, setBots, setRoomInfo, ROOMS };
})();

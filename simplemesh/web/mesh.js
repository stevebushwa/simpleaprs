// simplemesh page. The server sends events (see node.py); each one is an upsert, so applying one twice is harmless.
// Everything below comes off the air: only ever put it on the page with textContent.
const $ = id => document.getElementById(id);
const MM = window.MeshMap || new Proxy({}, {get: (t, k) => (k in t ? t[k] : () => {}), set: (t, k, v) => { t[k] = v; return true; }});
if (!window.MeshMap) $("nomap").style.display = "grid";
const mk = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
const store = {get: k => { try { return localStorage.getItem(k); } catch { return null; } },
               set: (k, v) => { try { localStorage.setItem(k, v); } catch {} }};
const TYPE = {0: "Unknown", 1: "Companion", 2: "Repeater", 3: "Room server", 4: "Sensor"};
const MAX_TEXT = 150;
const DAY = 86400;

const S = {status: {}, me: {}, channels: [], nodes: new Map(), msgs: new Map(), rxs: [], home: null};
const now = () => Date.now() / 1000;
const stamp = t => { const d = new Date(t * 1000);
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], {hour12: false, timeStyle: "short"}) : d.toLocaleString([], {hour12: false, dateStyle: "short", timeStyle: "short"}); };
const ago = t => {
  if (!t) return "never";
  const s = Math.max(0, now() - t);
  return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 2 * DAY ? Math.round(s / 3600) + " h ago" : Math.round(s / DAY) + " d ago";
};
// When we last heard a node. The node's own clock (advert) is a fallback, but only if it looks set.
const heard = n => n.heard || (n.advert > 1.7e9 ? n.advert : 0);

const toLogin = () => { location.href = "/login"; };
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (r.status === 401) { toLogin(); throw new Error("login required"); }
  return {ok: r.ok, status: r.status, body: await r.json().catch(() => ({}))};
};
const show = text => { const n = $("notice"); n.textContent = text || ""; n.hidden = !text; if (text) setTimeout(() => { if (n.textContent === text) n.hidden = true; }, 8000); };
const act = async (path, body) => {  // a request to the node; its refusal is shown, not thrown
  try { const r = await api(path, body); if (!r.ok) show(r.body.error || `The server answered ${r.status}`); return r; }
  catch { return {ok: false, body: {}}; }
};

// ---- routes ---------------------------------------------------------------------------------------------
const hopsOf = n => {
  if (!n.path || n.hops <= 0) return [];
  const w = (n.hash_size || 1) * 2, out = [];
  for (let i = 0; i < n.hops; i++) out.push(n.path.slice(i * w, (i + 1) * w));
  return out;
};
const byHash = hex => [...S.nodes.values()].filter(x => x.type === 2 && x.key.startsWith(hex));
const hopName = hex => { const c = byHash(hex); return c.length === 1 ? c[0].name : c.length ? hex + "?" : hex; };
// Positions to draw through: us, each hop we can place, the node. A hop we can't place makes the next line dashed.
const routeSegments = n => {
  if (!n.saved || n.lat == null || n.hops == null || n.hops < 0) return [];
  const stops = [];  // [lon, lat], or null for a hop with no known position
  if (S.me.lat != null) stops.push([S.me.lon, S.me.lat]);
  for (const hex of hopsOf(n)) {
    const c = byHash(hex).filter(x => x.lat != null);
    stops.push(c.length === 1 ? [c[0].lon, c[0].lat] : null);
  }
  stops.push([n.lon, n.lat]);
  const segs = [];
  let last = null, gap = false;
  for (const p of stops) {
    if (!p) { gap = true; continue; }
    if (last) segs.push({dashed: gap, coords: [last, p]});
    last = p; gap = false;
  }
  return segs;
};

// ---- applying events -----------------------------------------------------------------------------------
let dirty = new Set();
let queued = false;
const touch = (...what) => {
  what.forEach(w => dirty.add(w));
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; const d = dirty; dirty = new Set(); render(d); });
};

const apply = ev => {
  if (ev.status) { S.status = ev.status; touch("header"); }
  if (ev.self) { S.me = ev.self; touch("header", "map", "settings"); }
  if (ev.channels) { S.channels = ev.channels; touch("convs", "settings"); }
  if (ev.flash) { S.flash = ev.flash; touch("flash"); }
  if (ev.nodes) { for (const n of ev.nodes) S.nodes.set(n.key, n); touch("nodes", "map", "convs"); }
  if (ev.gone) { for (const k of ev.gone) S.nodes.delete(k); MM.remove(ev.gone); touch("nodes", "convs"); }
  if (ev.msgs) { for (const m of ev.msgs) S.msgs.set(m.id, m); touch("convs"); }
  if (ev.rxs) { for (const r of ev.rxs) { S.rxs.push(r); if (S.rxs.length > 400) S.rxs.shift(); } touch("packets"); }
  if (ev.notice) show(ev.notice);
};

// ---- rendering -------------------------------------------------------------------------------------------
let filter = store.get("filter") || "all";
$("filter").value = filter;
$("filter").onchange = e => { filter = e.target.value; store.set("filter", filter); touch("nodes", "map"); };
const visible = n => filter === "all" || (filter === "saved" ? n.saved : String(n.type) === filter);

const render = d => {
  if (d.has("header")) renderHeader();
  if (d.has("map")) renderMap();
  if (d.has("nodes")) renderNodes();
  if (d.has("convs")) renderConvs();
  if (d.has("packets")) renderPackets();
  if (d.has("settings")) settingsLive();
  if (d.has("flash")) updateFlash();
};

const renderHeader = () => {
  const s = S.status, m = S.me;
  $("conn").classList.toggle("on", !!s.connected);
  $("title").textContent = m.name && s.connected ? m.name : "simplemesh";
  $("status").textContent = s.text || "";
  $("advert").disabled = !s.connected;
  $("radio").textContent = s.connected && m.freq ? `${m.freq} MHz  BW ${m.bw} kHz  SF${m.sf}  CR 4/${m.cr}  ${m.tx_power} dBm` + (m.model ? `  -  ${m.model} ${m.version || ""}` : "") : "";
  $("battery").textContent = s.battery != null ? `Battery ${s.battery}% (${(s.battery_mv / 1000).toFixed(2)} V)` : "";
  for (const f of document.querySelectorAll(".compose")) for (const el of f.querySelectorAll("input, button")) el.disabled = !s.connected;
};

let fitted = false;
const renderMap = () => {
  MM.setMe(S.me.lat, S.me.lon, S.me.name);
  for (const n of S.nodes.values()) {
    const h = heard(n);
    MM.setNode(n, {visible: visible(n), old: h > 0 && now() - h > DAY});
  }
  if (!fitted) {  // once, when the first positions arrive: show them all (or home, if there is nothing yet)
    const pts = [...S.nodes.values()].filter(n => n.lat != null).map(n => [n.lat, n.lon]);
    if (S.me.lat != null) pts.push([S.me.lat, S.me.lon]);
    if (pts.length) { MM.fit(pts); fitted = true; }
    else if (S.home) { MM.jump(S.home[0], S.home[1], 10); }
  }
};

const nodeRow = n => {
  const tr = mk("tr", {className: heard(n) && now() - heard(n) > DAY ? "old" : ""});
  const name = mk("td", {}, mk("span", {className: "chip t" + n.type, textContent: TYPE[n.type] || "?"}), " ", n.name);
  if (!n.saved) {
    name.append(" ", mk("button", {className: "ghost small", textContent: "Add", title: "Save this node on your node",
      onclick: ev => { ev.stopPropagation(); act("/api/node/add", {key: n.key}); }}));
  }
  const route = n.hops == null ? "" : n.hops < 0 ? "flood" : n.hops === 0 ? "direct" : n.hops + (n.hops === 1 ? " hop" : " hops");
  tr.append(name, mk("td", {textContent: ago(heard(n))}), mk("td", {textContent: n.saved ? route : ""}),
            mk("td", {textContent: n.snr != null ? n.snr + " dB" : ""}));
  tr.onclick = () => {
    if (n.lat == null) return show(`${n.name} hasn't shared a position.`);
    MM.flyTo(n.lat, n.lon, 13); MM.open(n.key);
  };
  return tr;
};
const renderNodes = () => {
  const q = $("search").value.trim().toLowerCase();
  const list = [...S.nodes.values()].filter(n => visible(n) && (!q || n.name.toLowerCase().includes(q) || n.key.startsWith(q)))
    .sort((a, b) => heard(b) - heard(a));
  $("rows").replaceChildren(...list.map(nodeRow));
  $("nodecount").textContent = `${list.length} node${list.length === 1 ? "" : "s"}`;
};
$("search").oninput = renderNodes;

// ---- popup ------------------------------------------------------------------------------------------------
MM.popupFor = key => {
  const n = S.nodes.get(key);
  const box = mk("div");
  if (!n) return box;
  const route = n.hops == null ? "" : n.hops < 0 ? "flood (no direct route learned yet)" : n.hops === 0 ? "direct" :
    n.hops + (n.hops === 1 ? " hop: " : " hops: ") + hopsOf(n).map(hopName).join(" > ");
  const rows = [["Type", TYPE[n.type] || "?"], ["Heard", heard(n) ? `${ago(heard(n))} (${stamp(heard(n))})` : "never"],
    ["Route", n.saved ? route : ""], ["Signal", n.snr != null ? `${n.snr} dB SNR` + (n.rssi != null ? `, ${n.rssi} dBm` : "") : ""],
    ["Position", n.lat != null ? `${n.lat.toFixed(5)}, ${n.lon.toFixed(5)}` : ""], ["Key", n.key.slice(0, 16) + "..."],
    ["Status", n.saved ? "Saved on your node" : "Heard, not saved"]];
  const dl = mk("dl");
  for (const [k, v] of rows) if (v) dl.append(mk("dt", {textContent: k}), mk("dd", {textContent: v}));
  box.append(mk("h4", {textContent: n.name}), dl);
  const btn = (text, fn, cls = "") => box.append(mk("button", {textContent: text, className: cls, onclick: fn}));
  if (n.saved && n.type === 1) btn("Message", () => openDm(n.key.slice(0, 12)));
  if (n.saved && [2, 3, 4].includes(n.type)) btn(n.type === 3 ? "Open room" : "Manage", () => openAdmin(n.key));
  if (n.saved) btn("Trace route", async () => { const r = await act("/api/admin/route", {key: n.key}); if (r.ok) show(routeText(r.body.route)); }, "plain");
  if (n.saved) btn("Share link", async () => { const r = await act("/api/contact/export", {key: n.key}); if (r.ok) info(n.name + " contact link", r.body.uri, "Anyone can import this link to add the node as a contact."); }, "plain");
  if (!n.saved) btn("Add", () => act("/api/node/add", {key: n.key}));
  if (n.saved && n.hops >= 0) btn("Forget route", () => act("/api/node/reset-path", {key: n.key}), "plain");
  btn("Remove", () => { if (confirm(`Remove ${n.name}?`)) act("/api/node/remove", {key: n.key}); }, "plain");
  return box;
};
MM.onselect = key => {
  const n = key && S.nodes.get(key);
  MM.drawRoute(n ? routeSegments(n) : []);
};

// ---- conversations ----------------------------------------------------------------------------------------
// A conversation is a channel ("ch:0") or a person ("dm:<first 12 hex of their key>").
const convOf = m => m.kind === "chan" ? "ch:" + m.chan : "dm:" + m.peer;
const readAt = c => Number(store.get("read:" + c) || 0);
const view = {chan: null, dm: null};
const peerName = p => { const n = [...S.nodes.values()].find(x => x.key.startsWith(p)); return n ? n.name : p.slice(0, 8); };
const textBytes = s => new TextEncoder().encode(s).length;

const bubble = m => {
  const b = mk("div", {className: "b " + (m.dir === "out" ? "out" : "") + (m.status === "failed" ? " failed" : "")});
  if (m.dir !== "out" && m.from) b.append(mk("div", {className: "who", textContent: m.from}));
  b.append(mk("div", {textContent: m.text}));
  const bits = [stamp(m.ts)];
  if (m.dir === "in") {
    if (m.hops != null) bits.push(m.hops === 0 ? "direct" : m.hops + (m.hops === 1 ? " hop" : " hops"));
    if (m.snr != null) bits.push(m.snr + " dB");
  } else if (m.status) {
    bits.push({sending: "sending...", sent: "sent", acked: "delivered", failed: "not delivered - no reply from the node"}[m.status] || m.status);
  }
  b.append(mk("div", {className: "meta", textContent: bits.join("  -  ")}));
  return b;
};

const section = (kind, convs, current, setCurrent) => {
  const pills = $(kind + "-pills"), thread = $(kind + "-thread");
  pills.replaceChildren(...convs.map(c => {
    const unread = c.unread;
    const b = mk("button", {className: c.key === current ? "on" : "", textContent: c.label});
    if (unread && c.key !== current) b.append(mk("span", {className: "badge", textContent: unread}));
    b.onclick = () => { setCurrent(c.key); touch("convs"); };
    return b;
  }));
  const msgs = [...S.msgs.values()].filter(m => convOf(m) === current).sort((a, b) => a.ts - b.ts).slice(-300);
  const stick = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 30;
  if (!current) thread.replaceChildren(mk("div", {className: "empty", textContent: kind === "dm" ?
    "No direct messages yet. Click a companion on the map or in Nodes and press Message." : "No channels yet."}));
  else if (!msgs.length) thread.replaceChildren(mk("div", {className: "empty", textContent: "No messages yet."}));
  else thread.replaceChildren(...msgs.map(bubble));
  if (stick || thread.dataset.conv !== current) thread.scrollTop = thread.scrollHeight;
  thread.dataset.conv = current || "";
};

const renderConvs = () => {
  // unread: messages from others newer than the last time that conversation was on screen
  const unread = {};
  for (const m of S.msgs.values()) if (m.dir === "in" && m.ts > readAt(convOf(m))) unread[convOf(m)] = (unread[convOf(m)] || 0) + 1;
  const chans = S.channels.map(c => ({key: "ch:" + c.idx, label: c.name, unread: unread["ch:" + c.idx] || 0}));
  if (!view.chan || !chans.some(c => c.key === view.chan)) view.chan = chans.length ? chans[0].key : null;
  const peers = new Set([...S.msgs.values()].filter(m => m.kind === "dm").map(m => m.peer));
  if (view.dm && view.dm.startsWith("dm:")) peers.add(view.dm.slice(3));
  const dms = [...peers].map(p => ({key: "dm:" + p, label: peerName(p), unread: unread["dm:" + p] || 0}));
  if (!view.dm || !dms.some(c => c.key === view.dm)) view.dm = dms.length ? dms[0].key : null;
  section("chan", chans, view.chan, k => { view.chan = k; });
  section("dm", dms, view.dm, k => { view.dm = k; });
  // what is on screen is read
  const tab = currentTab();
  if (tab === "channels" && view.chan) store.set("read:" + view.chan, now());
  if (tab === "direct" && view.dm) store.set("read:" + view.dm, now());
  const sum = (list, key) => list.filter(c => !(currentTab() === key && c.key === (key === "channels" ? view.chan : view.dm))).reduce((a, c) => a + c.unread, 0);
  for (const [id, n] of [["b-channels", sum(chans, "channels")], ["b-direct", sum(dms, "direct")]]) {
    $(id).hidden = !n; $(id).textContent = n;
  }
  updateCounts();
};

const openDm = peer => {
  view.dm = "dm:" + peer;
  tabTo("direct");
  touch("convs");
  setTimeout(() => $("dm-text").focus(), 0);
};

const updateCounts = () => {
  const reserve = S.me.name ? textBytes(S.me.name) + 2 : 0;
  for (const [kind, extra] of [["chan", reserve], ["dm", 0]]) {
    const n = textBytes($(kind + "-text").value), max = MAX_TEXT - extra;
    const c = $(kind + "-count");
    c.textContent = n > max * 0.6 ? `${n}/${max}` : "";
    c.classList.toggle("over", n > max);
  }
};
for (const k of ["chan", "dm"]) $(k + "-text").oninput = updateCounts;

const sender = (kind, getTarget) => async e => {
  e.preventDefault();
  const input = $(kind + "-text"), text = input.value.trim(), target = getTarget();
  if (!text || !target) return;
  const body = kind === "chan" ? {kind: "chan", chan: Number(target.slice(3)), text} : {kind: "dm", to: target.slice(3), text};
  input.disabled = true;
  const r = await act("/api/send", body);
  input.disabled = !S.status.connected;
  if (r.ok) { input.value = ""; updateCounts(); if (r.body.msg) apply({msgs: [r.body.msg]}); }
  input.focus();
};
$("chan-form").onsubmit = sender("chan", () => view.chan);
$("dm-form").onsubmit = sender("dm", () => view.dm);

// ---- packets ----------------------------------------------------------------------------------------------
let shownRx = 0;
$("hide-adverts").checked = store.get("hide-adverts") === "1";
$("hide-adverts").onchange = e => { store.set("hide-adverts", e.target.checked ? "1" : "0"); shownRx = 0; $("pkts").textContent = ""; renderPackets(); };
const rxLine = r => {
  const d = mk("div");
  const via = r.hops > 0 ? " via " + (r.path ? Array.from({length: r.hops}, (_, i) => hopName(r.path.slice(i * r.hash_size * 2, (i + 1) * r.hash_size * 2))).join(" > ") : r.hops + " hops") : (r.route === "DIRECT" ? " (direct)" : "");
  const what = r.kind === "ADVERT" ? (r.name || (r.key ? r.key.slice(0, 8) : "")) : r.chan ? "#" + r.chan.replace(/^#/, "") : "";
  d.append(mk("span", {className: "t", textContent: new Date(r.t * 1000).toLocaleTimeString([], {hour12: false})}),
           mk("span", {className: "kind " + r.kind, textContent: r.kind}), what + via, " ",
           mk("span", {className: "dim", textContent: [r.snr != null ? r.snr + " dB" : "", r.rssi != null ? r.rssi + " dBm" : "", r.size + " B"].filter(Boolean).join("  ")}));
  return d;
};
const renderPackets = () => {
  const box = $("pkts"), hide = $("hide-adverts").checked;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  if (S.rxs.length < shownRx) { shownRx = 0; box.textContent = ""; }
  for (const r of S.rxs.slice(shownRx)) if (!(hide && r.kind === "ADVERT")) box.append(rxLine(r));
  shownRx = S.rxs.length;
  while (box.childElementCount > 400) box.firstChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
  $("rxcount").textContent = S.rxs.length ? `${S.rxs.length} heard` : "";
};

// ---- tabs -------------------------------------------------------------------------------------------------
const currentTab = () => document.querySelector("nav button.on").dataset.tab;
const tabTo = name => {
  for (const o of document.querySelectorAll("nav button")) o.classList.toggle("on", o.dataset.tab === name);
  for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== name;
  store.set("tab", name);
  for (const l of document.querySelectorAll(".log, .thread")) l.scrollTop = l.scrollHeight;
  if (name === "settings") renderSettings();
  touch("convs");
};
for (const b of document.querySelectorAll("nav button")) b.onclick = () => tabTo(b.dataset.tab);
$("advert").onclick = async () => { const r = await act("/api/advert", {flood: false}); if (r.ok) show("Advert sent to the nodes that can hear you directly."); };
$("logout").onclick = async () => { try { await api("/api/logout", {}); } catch {} toLogin(); };

// ---- start ------------------------------------------------------------------------------------------------
// (once manage.js has loaded too: the settings tab and the dialogs live there)
const boot = () => {
  tabTo(["channels", "direct", "nodes", "packets", "settings"].includes(store.get("tab")) ? store.get("tab") : "channels");
  api("/api/settings").then(r => { S.home = r.body.home; touch("map"); }).catch(() => {});
  const connect = () => {
    const es = new EventSource("/web/events");
    es.onmessage = e => { try { apply(JSON.parse(e.data)); } catch (err) { console.log("bad event", err); } };
    es.onerror = async () => {
      S.status = {...S.status, connected: false, text: "Lost the connection to the server. Reconnecting..."}; touch("header");
      try { const r = await fetch("/api/auth/state"); if (r.ok && !(await r.json()).logged_in) toLogin(); } catch {}
    };
  };
  connect();
  setInterval(() => touch("nodes"), 30000);  // "5 min ago" moves on its own
};
document.addEventListener("DOMContentLoaded", boot);

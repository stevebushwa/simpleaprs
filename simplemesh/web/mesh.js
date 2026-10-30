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
const hopsFrom = (path, hops, size) => {
  if (!path || !(hops > 0)) return [];
  const w = (size || 1) * 2, out = [];
  for (let i = 0; i < hops; i++) out.push(path.slice(i * w, (i + 1) * w));
  return out;
};
const hopsOf = n => hopsFrom(n.path, n.hops, n.hash_size);
const routeWords = (hops, list) => hops === 0 ? "direct" : hops + (hops === 1 ? " hop: " : " hops: ") + list.map(hopName).join(" > ");
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

let synced = false;  // the history the server sends first is not news; what comes after it is
const apply = ev => {
  if (ev.status) { const since = S.status.since; S.status = ev.status; touch("header"); if (ev.status.since !== since) touch("nodes", "map"); }
  if (ev.self) { S.me = ev.self; touch("header", "map", "settings"); }
  if (ev.channels) { S.channels = ev.channels; touch("convs", "settings"); }
  if (ev.flash) { S.flash = ev.flash; touch("flash"); }
  if (ev.nodes) { for (const n of ev.nodes) S.nodes.set(n.key, n); touch("nodes", "map", "convs"); }
  if (ev.gone) { for (const k of ev.gone) S.nodes.delete(k); MM.remove(ev.gone); touch("nodes", "convs"); }
  if (ev.msgs) {
    for (const m of ev.msgs) { const fresh = !S.msgs.has(m.id); S.msgs.set(m.id, m); if (fresh && synced && m.dir === "in") alertFor(m); }
    touch("convs");
  }
  if (ev.sync) synced = true;
  if (ev.rxs) { for (const r of ev.rxs) { S.rxs.push(r); if (S.rxs.length > 400) S.rxs.shift(); } touch("packets"); }
  if (ev.notice) show(ev.notice);
};

// ---- rendering -------------------------------------------------------------------------------------------
let filter = store.get("filter") || "all";
$("filter").value = filter;
$("filter").onchange = e => { filter = e.target.value; store.set("filter", filter); touch("nodes", "map"); };
// A node is shown grey until it has been heard since the node connected (what this page remembers from before is only
// where it was last), and again when it has been quiet for a day.
const quiet = n => { const h = heard(n); return !h || h < (S.status.since || 0) || now() - h > DAY; };
const visible = n => filter === "all" || (filter === "saved" ? n.saved : String(n.type) === filter);

const render = d => {
  if (d.has("header")) { renderHeader(); renderContacts(); }
  if (d.has("map")) renderMap();
  if (d.has("nodes")) { renderNodes(); renderContacts(); }
  if (d.has("convs")) renderConvs();
  if (d.has("packets")) renderPackets();
  if (d.has("settings")) settingsLive();
  if (d.has("flash")) updateFlash();
};

// how full the node's contact list is: red from 80%
const renderContacts = () => {
  const cap = S.me.max_contacts, used = [...S.nodes.values()].filter(n => n.saved).length, el = $("contacts");
  el.textContent = S.status.connected && cap ? `Contacts ${used}/${cap}` : "";
  el.classList.toggle("nearly", !!cap && used >= cap * 0.8);
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
    MM.setNode(n, {visible: visible(n), old: quiet(n)});
  }
  if (!fitted) {  // once, when the first positions arrive: show them all (or home, if there is nothing yet)
    const pts = [...S.nodes.values()].filter(n => n.lat != null).map(n => [n.lat, n.lon]);
    if (S.me.lat != null) pts.push([S.me.lat, S.me.lon]);
    if (pts.length) { MM.fit(pts); fitted = true; }
    else if (S.home) { MM.jump(S.home[0], S.home[1], 10); }
  }
};

const nodeRow = n => {
  const tr = mk("tr", {className: quiet(n) ? "old" : ""});
  const name = mk("td", {}, mk("span", {className: "chip t" + n.type, textContent: TYPE[n.type] || "?"}), " ", n.name);
  if (!n.saved) {
    name.append(" ", mk("button", {className: "ghost small", textContent: "Add", title: "Save this node on your node",
      onclick: ev => { ev.stopPropagation(); act("/api/node/add", {key: n.key}); }}));
  }
  const route = n.hops == null ? "" : n.hops < 0 ? "flood" : n.hops === 0 ? "direct" : n.hops + (n.hops === 1 ? " hop" : " hops");
  tr.append(name, mk("td", {textContent: ago(heard(n))}), mk("td", {textContent: n.saved ? route : ""}),
            mk("td", {textContent: n.snr != null ? n.snr + " dB" : "", title: n.heard_hops > 0 ? `The last hop's signal: this node's advert came ${n.heard_hops} hops` : ""}));
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
  const route = n.hops == null ? "" : n.hops < 0 ? "flood (no direct route learned yet)" : routeWords(n.hops, hopsOf(n));
  const remembered = n.last_hops != null && !(n.saved && n.hops >= 0);  // a route it had once, that the node doesn't have now
  const signal = n.snr == null ? "" : `${n.snr} dB SNR` + (n.rssi != null ? `, ${n.rssi} dBm` : "") +
    (n.heard_hops > 0 ? ` (the last of ${n.heard_hops} hops, not this node's own)` : n.heard_hops === 0 ? " (direct)" : "");
  const rows = [["Type", TYPE[n.type] || "?"], ["Heard", heard(n) ? `${ago(heard(n))} (${stamp(heard(n))})` : "never"],
    ["Route", n.saved ? route : ""], ["Last route", remembered ? routeWords(n.last_hops, hopsFrom(n.last_path, n.last_hops, n.last_hash)) + (n.last_path_at ? ` (${ago(n.last_path_at)})` : "") : ""],
    ["Signal", signal],
    ["Position", n.lat != null ? `${n.lat.toFixed(5)}, ${n.lon.toFixed(5)}` : ""], ["Key", n.key.slice(0, 16) + "..."],
    ["Status", n.saved ? "Saved on your node" : "Kept here, not on your node"]];
  const dl = mk("dl");
  for (const [k, v] of rows) if (v) dl.append(mk("dt", {textContent: k}), mk("dd", {textContent: v}));
  box.append(mk("h4", {textContent: n.name}), dl);
  const btn = (text, fn, cls = "") => box.append(mk("button", {textContent: text, className: cls, onclick: fn}));
  if (n.saved && n.type === 1) btn("Message", () => openDm(n.key.slice(0, 12)));
  if (n.saved && [2, 3, 4].includes(n.type)) btn(n.type === 3 ? "Open room" : "Manage", () => openAdmin(n.key));
  if (n.saved) btn("Find route", async () => { const r = await act("/api/admin/route", {key: n.key}); if (r.ok) show(routeText(r.body.route)); }, "plain");
  if (n.saved) btn("Share link", async () => { const r = await act("/api/contact/export", {key: n.key}); if (r.ok) info(n.name + " contact link", r.body.uri, "Anyone can import this link to add the node as a contact."); }, "plain");
  if (!n.saved) btn("Add", () => act("/api/node/add", {key: n.key}));
  if (n.saved && n.hops < 0 && n.last_hops != null) btn("Use last route", () => act("/api/node/restore-route", {key: n.key}), "plain");
  if (n.saved && n.hops >= 0) btn("Forget route", () => act("/api/node/reset-path", {key: n.key}), "plain");
  if (n.saved) btn("Remove from node", () => { if (confirm(`Remove ${n.name} from your node? It stays here, with its last route, and can be added back.`)) act("/api/node/remove", {key: n.key}); }, "plain");
  else btn("Forget", () => { if (confirm(`Forget ${n.name}? It is removed from here as well.`)) act("/api/node/remove", {key: n.key, forget: true}); }, "plain");
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
  const meta = mk("div", {className: "meta", textContent: bits.join("  -  ")});
  if (m.kind === "chan" && m.dir === "in" && m.from) {  // channels have no threads: a reply is a message that names who it answers
    meta.append(mk("button", {className: "reply", textContent: "Reply", title: "Start a message to " + m.from, onclick: () => {
      const input = $("chan-text"), tag = `@[${m.from}] `;
      if (!input.value.startsWith(tag)) input.value = tag + input.value;
      updateCounts(); input.focus();
    }}));
  }
  b.append(meta);
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
    c.textContent = n ? `${n}/${max}` : "";
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
const tabTo = (name, open = true) => {
  if (open) {  // picking a tab opens the panel if it was shut
    if (main.classList.contains("shut")) { store.set("shut", "0"); shut(false); }
    if (!narrow.matches && sideWidth() === 0) toggleSide();
  }
  for (const o of document.querySelectorAll("nav button")) o.classList.toggle("on", o.dataset.tab === name);
  for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== name;
  store.set("tab", name);
  for (const l of document.querySelectorAll(".log, .thread")) l.scrollTop = l.scrollHeight;
  if (name === "settings") renderSettings();
  touch("convs");
};
for (const b of document.querySelectorAll("nav button[data-tab]")) b.onclick = () => tabTo(b.dataset.tab);
$("advert").onclick = async () => { const r = await act("/api/advert", {flood: false}); if (r.ok) show("Advert sent to the nodes that can hear you directly."); };
$("logout").onclick = async () => { try { await api("/api/logout", {}); } catch {} toLogin(); };

// ---- the panel beside the map ---------------------------------------------------------------------------------
// On a computer its edge can be dragged to any width (or tapped to hide it); on a phone it sits under the map and the
// button in the tab bar shuts it down to the tabs or opens it up again.
const main = document.querySelector("main"), split = $("split"), fold = $("fold"), narrow = matchMedia("(max-width: 800px)");
const setSide = (px, save) => {
  px = Math.max(0, Math.min(Math.round(px), main.clientWidth - split.offsetWidth - 80));
  main.style.setProperty("--side", px + "px");
  if (save) store.set("side_w", px);
  return px;
};
const sideWidth = () => document.querySelector("aside").offsetWidth;
const toggleSide = () => {
  if (sideWidth() > 0) { store.set("side_prev", sideWidth()); setSide(0, true); }
  else setSide(+store.get("side_prev") || 420, true);
};
split.onpointerdown = e => {
  e.preventDefault();
  split.setPointerCapture(e.pointerId);
  const start = e.clientX, from = sideWidth();
  let moved = false;
  main.classList.add("dragging");
  split.onpointermove = m => {
    if (Math.abs(m.clientX - start) > 4) moved = true;
    if (moved) setSide(from - (m.clientX - start), false);
  };
  split.onpointerup = split.onpointercancel = () => {
    split.onpointermove = split.onpointerup = split.onpointercancel = null;
    main.classList.remove("dragging");
    if (moved) store.set("side_w", sideWidth()); else toggleSide();
  };
};
split.onkeydown = e => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSide(); }
  else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); setSide(sideWidth() + (e.key === "ArrowLeft" ? 40 : -40), true); }
};
if (store.get("side_w") !== null) setSide(+store.get("side_w"), false);
addEventListener("resize", () => { if (store.get("side_w") !== null) setSide(+store.get("side_w"), false); });
const shut = on => {
  main.classList.toggle("shut", on);
  fold.innerHTML = on ? "&#9652;" : "&#9662;";
  fold.setAttribute("aria-label", on ? "Open the panel" : "Minimize the panel");
};
shut(store.get("shut") === "1");
fold.onclick = () => { const on = !main.classList.contains("shut"); store.set("shut", on ? "1" : "0"); shut(on); };
new ResizeObserver(() => MM.resize()).observe($("mapbox"));  // the map follows the panel as it grows and shrinks

// ---- alerts ----------------------------------------------------------------------------------------------------
// A message that arrives gets a card on the map and, with the bell on, a chime, a vibration and (if this page isn't the
// one in front) a notification through the system, which a phone shows too once the page is installed as an app. These
// are this browser's own choices (Settings > Alerts), since a phone and a computer want different things.
const prefs = {card: true, chime: true, vibrate: true, notify: true, channels: true, direct: true};
try { Object.assign(prefs, JSON.parse(store.get("alertPrefs") || "{}")); } catch {}
const setPref = (key, on) => { prefs[key] = on; store.set("alertPrefs", JSON.stringify(prefs)); };
let bell = store.get("bell") === "on", audio = null, lastChime = 0, worker = null;
const unlockAudio = () => { try { audio ??= new AudioContext(); audio.resume(); } catch {} };
const chime = () => {
  if (Date.now() - lastChime < 1500) return;  // a batch of missed messages is one chime, not a burst
  lastChime = Date.now(); unlockAudio();
  try {
    const t = audio.currentTime;
    [[880, 0], [1175, 0.18]].forEach(([hz, at]) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = hz; o.connect(g).connect(audio.destination);
      g.gain.setValueAtTime(0.0001, t + at); g.gain.exponentialRampToValueAtTime(0.3, t + at + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.4);
      o.start(t + at); o.stop(t + at + 0.45);
    });
  } catch {}
};
const notifyProblem = () => !("Notification" in window) ? "this browser has no notifications"
  : !window.isSecureContext ? "browsers only allow notifications on https pages (or localhost)"
  : Notification.permission === "denied" ? "notifications are blocked for this site in the browser's settings" : "";
// Registered as the page loads, not when the first notification is shown: browsers want a service worker before they offer to install the page.
worker = ("serviceWorker" in navigator && window.isSecureContext)
  ? navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready).catch(() => null) : Promise.resolve(null);
const notify = async (title, body, tag, force = false) => {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!force && document.visibilityState === "visible" && document.hasFocus()) return;  // the card and chime are enough while it's in front
  const opts = {body, tag, icon: "/icon-192.png", vibrate: [200, 100, 200]};  // a tag of its own: two messages are two notifications, not one replacing the other
  try { const reg = await worker; if (reg) await reg.showNotification(title, opts); else new Notification(title, opts); } catch {}
};
const setBell = on => {
  bell = on; store.set("bell", on ? "on" : "off");
  $("bell").textContent = on ? "Alerts on" : "Alerts"; $("bell").classList.toggle("on", on);
  if (document.querySelector("nav button.on")) touch("settings");  // (not before the page has started)
};
const askNotifications = async () => {
  if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission().catch(() => {});
  touch("settings");
};
$("bell").onclick = async () => {
  if (bell) return setBell(false);
  unlockAudio(); chime(); navigator.vibrate?.(150);  // a taste of it, and the tap the browser wants before it allows either
  setBell(true);
  if (prefs.notify) await askNotifications();
  const why = prefs.notify ? notifyProblem() : "";
  show(why ? `Alerts on: sound and vibration only (${why})` : "Alerts on");
};
setBell(bell);
if (bell) document.addEventListener("pointerdown", unlockAudio, {once: true});  // a reload needs a touch before the browser lets it make sound
const chanName = idx => (S.channels.find(c => c.idx === idx) || {}).name || "channel " + idx;
const describe = m => m.kind === "chan"
  ? {title: `${m.from || "Someone"} in ${chanName(m.chan)}`, body: m.text}
  : {title: `Message from ${m.from || peerName(m.peer)}`, body: m.text};
const onScreen = m => document.visibilityState === "visible" && document.hasFocus() && !main.classList.contains("shut")
  && currentTab() === (m.kind === "chan" ? "channels" : "direct") && view[m.kind === "chan" ? "chan" : "dm"] === convOf(m);
// One card per conversation: more messages in it count up on the card it already has, rather than stacking new ones.
const cardFor = new Map();  // conversation -> {card, n, m}; a card that was dismissed is no longer in the page
const showCard = (m, d) => {
  const key = convOf(m), have = cardFor.get(key);
  if (have && have.card.isConnected) {
    have.n++; have.m = m;
    const [title, time, body] = have.card.querySelectorAll("h4 span:first-child, h4 span:last-child, .body");
    title.textContent = `${have.n} new messages ` + (m.kind === "chan" ? "in " + chanName(m.chan) : "from " + (m.from || peerName(m.peer)));
    time.textContent = stamp(now());
    body.textContent = m.kind === "chan" ? `${m.from || "Someone"}: ${m.text}` : m.text;  // the latest of them
    $("cards").prepend(have.card);
    return;
  }
  const card = mk("div", {className: "card"}), rec = {card, n: 1, m};
  const reply = mk("button", {className: "act small", textContent: "Reply", onclick: () => {
    const m = rec.m;
    if (m.kind === "chan") { view.chan = convOf(m); tabTo("channels"); touch("convs"); const i = $("chan-text"), tag = m.from ? `@[${m.from}] ` : ""; if (tag && !i.value.startsWith(tag)) i.value = tag + i.value; updateCounts(); i.focus(); }
    else openDm(m.peer);
    card.remove();
  }});
  card.append(mk("h4", {}, mk("span", {textContent: d.title}), mk("span", {textContent: stamp(now())})), mk("div", {className: "body", textContent: d.body}),
              mk("div", {className: "actions"}, reply, mk("button", {className: "ghost small", textContent: "Dismiss", onclick: () => card.remove()})));
  cardFor.set(key, rec);
  $("cards").prepend(card);
  while ($("cards").childElementCount > 5) $("cards").lastChild.remove();  // the rest are in the conversations
};
const alertFor = m => {
  if (!(m.kind === "chan" ? prefs.channels : prefs.direct) || onScreen(m)) return;
  const d = describe(m);
  if (prefs.card) showCard(m, d);
  if (!bell) return;
  if (prefs.chime) chime();
  if (prefs.vibrate) navigator.vibrate?.([200, 100, 200]);
  if (prefs.notify) notify(d.title, d.body, "msg-" + m.id);
};

// ---- start ------------------------------------------------------------------------------------------------
// (once manage.js has loaded too: the settings tab and the dialogs live there)
const boot = () => {
  tabTo(["channels", "direct", "nodes", "packets", "settings"].includes(store.get("tab")) ? store.get("tab") : "channels", false);
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
  setInterval(() => touch("nodes", "map"), 30000);  // "5 min ago" moves on its own, and a node goes grey once it has been quiet a day
};
document.addEventListener("DOMContentLoaded", boot);

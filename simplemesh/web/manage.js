// simplemesh page, part two: managing the node (the Settings tab), reaching other nodes (the admin dialog) and updating
// firmware. Loaded after mesh.js, which starts the page once the document is ready and calls into here.
// Everything that came off the air is put on the page with textContent only.
const SECTIONS = [["node", "Node"], ["radio", "Radio"], ["channels", "Channels"], ["firmware", "Firmware"], ["alerts", "Alerts"], ["connection", "Connection"]];
let pane = SECTIONS.some(s => s[0] === store.get("pane")) ? store.get("pane") : "node";
let editing = false;   // someone is typing in a settings form: don't redraw it under them
let fw = {};           // handles on the live parts of the firmware section

const call = async (path, body) => { try { return await api(path, body); } catch { return {ok: false, body: {error: "The server didn't answer."}}; } };
const fld = (label, ctl, hint) => mk("div", {className: "fld"}, mk("label", {className: "t", textContent: label}), ctl, ...(hint ? [mk("div", {className: "hint", textContent: hint})] : []));
const sel = (options, value) => { const s = mk("select"); for (const [v, t] of options) s.append(mk("option", {value: v, textContent: t})); s.value = value; return s; };
const button = (text, onclick, cls = "ghost") => mk("button", {type: "button", className: cls, textContent: text, onclick});
const say = (box, r, okText) => {  // a saved setting redraws the form, so the good news also goes up top, where it stays
  box.className = r.ok ? "ok" : "err"; box.textContent = r.ok ? okText : (r.body.error || "The node refused.");
  if (r.ok && okText) show(okText);
};
const withBusy = async (btn, fn) => { btn.disabled = true; try { return await fn(); } finally { btn.disabled = false; } };
const kv = rows => { const dl = mk("dl", {className: "kv"}); for (const [k, v] of rows) if (v != null && v !== "") dl.append(mk("dt", {textContent: k}), mk("dd", {textContent: String(v)})); return dl; };
const dur = s => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "d " : "") + (d || h ? h + "h " : "") + m + "m"; };
const range = (a, b) => Array.from({length: b - a + 1}, (_, i) => a + i);

// ---- a box to copy text out of -------------------------------------------------------------------------------
const info = (title, text, note) => {
  $("info-title").textContent = title; $("info-text").value = text; $("info-note").textContent = note || "";
  $("info").showModal(); $("info-text").select();
};
$("info-close").onclick = () => $("info").close();
$("info-copy").onclick = async () => {  // navigator.clipboard needs https; this page is usually plain http on a LAN
  $("info-text").select();
  try { await navigator.clipboard.writeText($("info-text").value); } catch { document.execCommand("copy"); }
  $("info-copy").textContent = "Copied"; setTimeout(() => { $("info-copy").textContent = "Copy"; }, 1500);
};

// ---- settings: the frame ------------------------------------------------------------------------------------
$("settings").addEventListener("input", () => { editing = true; });
$("settings").addEventListener("change", () => { editing = true; });
const settingsLive = () => { if (currentTab() === "settings" && !editing) renderSettings(); };

const renderSettings = async () => {
  editing = false;
  const root = $("settings");
  let cfg;
  try { cfg = (await api("/api/settings")).body; } catch { return; }
  S.home = cfg.home;
  const pills = mk("div", {className: "pills sub"}, ...SECTIONS.map(([id, label]) =>
    mk("button", {className: id === pane ? "on" : "", textContent: label, onclick: () => { pane = id; store.set("pane", id); renderSettings(); }})));
  const draw = {node: secNode, radio: secRadio, channels: secChannels, firmware: secFirmware, alerts: secAlerts, connection: secConnection}[pane];
  fw = {};
  root.replaceChildren(pills, draw(cfg));
  updateFlash();
};
const needNode = () => S.status.connected ? null : mk("div", {className: "hint", textContent: "Connect a node to use this."});

// ---- settings: node -----------------------------------------------------------------------------------------
const secNode = cfg => {
  if (!S.status.connected) return needNode();
  const box = mk("div");
  // name and position
  const name = mk("input", {type: "text", maxLength: 31, autocomplete: "off", value: S.me.name || ""});
  const lat = mk("input", {type: "number", step: "0.00001", value: S.me.lat ?? ""}), lon = mk("input", {type: "number", step: "0.00001", value: S.me.lon ?? ""});
  const meMsg = mk("div", {className: "err"});
  const save = button("Save to node", () => withBusy(save, async () => {
    const r = await call("/api/profile", {name: name.value, lat: Number(lat.value || 0), lon: Number(lon.value || 0)});
    say(meMsg, r, "Saved."); if (r.ok) editing = false;
  }), "act");
  const center = button("Use map centre", () => { const c = MM.center(); if (c) { lat.value = c.lat.toFixed(5); lon.value = c.lng.toFixed(5); editing = true; } });
  box.append(mk("h3", {textContent: "This node"}), fld("Node name", name, "How other nodes see you. Sent in adverts."),
    fld("Position", mk("div", {className: "row"}, lat, lon), "Sent in adverts so others can put you on their map. Leave 0, 0 to hide it."),
    mk("div", {className: "btns"}, center, save), meMsg);

  // advertising
  box.append(mk("h3", {textContent: "Advertising"}), mk("div", {className: "btns"},
    button("Advertise (neighbours)", () => act("/api/advert", {flood: false})),
    button("Advertise (whole mesh)", () => confirm("A flood advert is repeated by every repeater that hears it. Fine now and then, bad as a habit. Send it?") && act("/api/advert", {flood: true}))));

  // privacy
  const manual = mk("input", {type: "checkbox", checked: !!S.me.manual_add});
  const policy = sel([["0", "Don't share my position"], ["1", "Share my position"]], String(S.me.loc_policy === 1 ? 1 : 0));
  const tel = [["tel_base", "Battery and basic status"], ["tel_loc", "Position"], ["tel_env", "Sensor readings"]].map(([k, label]) =>
    [k, label, sel([["0", "Nobody"], ["1", "Contacts I've flagged"], ["2", "Everyone"]], String(S.me[k] ?? 0))]);
  const pMsg = mk("div", {className: "err"});
  const pSave = button("Save", () => withBusy(pSave, async () => {
    const r = await call("/api/privacy", {manual_add: manual.checked, loc_policy: Number(policy.value),
      tel_base: Number(tel[0][2].value), tel_loc: Number(tel[1][2].value), tel_env: Number(tel[2][2].value)});
    say(pMsg, r, "Saved."); if (r.ok) editing = false;
  }), "act");
  box.append(mk("h3", {textContent: "Contacts and privacy"}),
    mk("label", {className: "fld"}, manual, " Ask before saving nodes that advertise", mk("div", {className: "hint", textContent: "On: heard nodes show in the list with an Add button. Off: the node saves them itself, until its contact list is full."})),
    fld("Position in adverts", policy), mk("div", {className: "t", textContent: "Who may ask this node for:", style: "font-weight:600;margin-bottom:3px"}),
    ...tel.map(([, label, ctl]) => fld(label, ctl)), mk("div", {className: "btns"}, pSave), pMsg);

  // contact links
  const link = mk("input", {type: "text", readOnly: true, placeholder: "meshcore://..."}), imp = mk("input", {type: "text", placeholder: "Paste a meshcore:// link", autocomplete: "off", spellcheck: false});
  const lMsg = mk("div", {className: "err"});
  box.append(mk("h3", {textContent: "Contact links"}),
    mk("div", {className: "btns"}, button("Show my contact link", async () => {
      const r = await act("/api/contact/export", {}); if (r.ok) info("My contact link", r.body.uri, "Anyone can import this link, here or in the MeshCore app, to add this node as a contact.");
    })), fld("Add someone else's", imp, "Paste a link a friend shared (it adds them as a contact)."),
    mk("div", {className: "btns"}, button("Import", async () => { const r = await call("/api/contact/import", {uri: imp.value}); say(lMsg, r, "Imported."); if (r.ok) imp.value = ""; })), lMsg);

  // statistics
  const out = mk("div");
  box.append(mk("h3", {textContent: "Statistics"}), mk("div", {className: "btns"}, button("Read from node", async ev => {
    const r = await act("/api/stats", {}); if (!r.ok) return;
    const c = r.body.core || {}, ra = r.body.radio || {}, p = r.body.packets || {};
    out.replaceChildren(kv([["Battery", c.battery_mv ? (c.battery_mv / 1000).toFixed(2) + " V" : ""], ["Uptime", c.uptime_secs != null ? dur(c.uptime_secs) : ""],
      ["Noise floor", ra.noise_floor != null ? ra.noise_floor + " dBm" : ""], ["Last signal", ra.last_rssi != null ? `${ra.last_snr} dB SNR, ${ra.last_rssi} dBm` : ""],
      ["Airtime", ra.tx_air_secs != null ? `${dur(ra.tx_air_secs)} sending, ${dur(ra.rx_air_secs)} listening` : ""],
      ["Packets", p.recv != null ? `${p.recv} heard, ${p.sent} sent` : ""], ["Flood / direct", p.recv != null ? `${p.flood_rx} / ${p.direct_rx} heard, ${p.flood_tx} / ${p.direct_tx} sent` : ""],
      ["Damaged packets", p.recv_errors], ["Errors", c.errors]]));
  })), out);

  // maintenance
  const keyBox = mk("textarea", {rows: 3, placeholder: "Paste a saved key here to restore it", spellcheck: false, autocomplete: "off", style: "width:100%"});
  const mMsg = mk("div", {className: "err"});
  box.append(mk("h3", {textContent: "Maintenance"}), mk("div", {className: "btns"},
    button("Restart the node", async () => { if (confirm("Restart the node? It is back in about ten seconds.")) { const r = await call("/api/node/reboot", {}); say(mMsg, r, "Restarting..."); } }),
    button("Back up its key", async () => {
      if (!confirm("The key is the node's identity: anyone who has it can pretend to be this node. Show it, to save somewhere private?")) return;
      const r = await call("/api/identity/export", {});
      if (r.ok) info("This node's key", r.body.key, "Keep this private. To restore it later, paste it into 'Restore a key' below; the node restarts and is the same node again.");
      else say(mMsg, r, "");
    })),
    fld("Restore a key", keyBox, "Replaces this node's identity with a saved one, then restarts it. Only do this to bring back a backup."),
    mk("div", {className: "btns"}, button("Restore key", async () => {
      if (!keyBox.value.trim()) return;
      if (!confirm("Replace this node's identity? Other nodes know it by its old key, and a wrong key can't be undone.")) return;
      const r = await call("/api/identity/import", {key: keyBox.value}); say(mMsg, r, "Restored. The node is restarting."); if (r.ok) keyBox.value = "";
    }, "ghost danger")), mMsg);
  return box;
};

// ---- settings: radio ----------------------------------------------------------------------------------------
const inNorthAmerica = () => {
  const la = S.me.lat ?? (S.home && S.home[0]), lo = S.me.lon ?? (S.home && S.home[1]);
  return la != null && la > 15 && la < 72 && lo < -50 && lo > -170;
};
const secRadio = cfg => {
  if (!S.status.connected) return needNode();
  const m = S.me, presets = cfg.presets;
  const freq = mk("input", {type: "number", step: "0.001", min: 150, max: 960, value: m.freq});
  const bw = sel(cfg.bandwidths.map(b => [String(b), b + " kHz"]), String(m.bw));
  const sf = sel(range(5, 12).map(v => [String(v), "SF" + v]), String(m.sf));
  const cr = sel(range(5, 8).map(v => [String(v), `4/${v}`]), String(m.cr));
  const power = mk("input", {type: "number", min: 1, max: m.max_tx_power || 22, value: m.tx_power});
  const match = () => presets.findIndex(p => p.freq === Number(freq.value) && String(p.bw) === bw.value && String(p.sf) === sf.value && String(p.cr) === cr.value);
  const preset = sel([["", "Custom"], ...presets.map((p, i) => [String(i), `${p.name} (${p.freq} MHz)`])], String(match() < 0 ? "" : match()));
  const warn = mk("div", {className: "warn"});
  const msg = mk("div", {className: "err"});
  const check = () => {
    const f = Number(freq.value);
    warn.hidden = !(inNorthAmerica() && (f < 902 || f > 928));
    warn.textContent = `${f} MHz is outside the 902-928 MHz band used in the US and Canada, and this node looks to be in North America. ` +
      "Transmitting there may be illegal. The USA / Canada preset is 910.525 MHz.";
    const i = match(); preset.value = i < 0 ? "" : String(i);
  };
  preset.onchange = () => {
    const p = presets[Number(preset.value)]; if (!p) return;
    freq.value = p.freq; bw.value = String(p.bw); sf.value = String(p.sf); cr.value = String(p.cr); check();
  };
  for (const el of [freq, bw, sf, cr]) el.addEventListener("input", check);
  check();
  const save = button("Save to node", () => withBusy(save, async () => {
    const changed = Number(freq.value) !== m.freq || Number(bw.value) !== m.bw || Number(sf.value) !== m.sf || Number(cr.value) !== m.cr;
    if (changed && !confirm(`Change the radio to ${freq.value} MHz, ${bw.value} kHz, SF${sf.value}, 4/${cr.value}?\n\nThe node will only hear nodes that use exactly the same four settings.`)) return;
    const r = await call("/api/radio", {freq: Number(freq.value), bw: Number(bw.value), sf: Number(sf.value), cr: Number(cr.value), tx_power: Number(power.value)});
    say(msg, r, "Saved."); if (r.ok) editing = false;
  }), "act");
  return mk("div", {}, mk("h3", {textContent: "Radio"}),
    mk("div", {className: "hint", textContent: "Nodes only hear each other when the frequency, bandwidth, spreading factor and coding rate all match. Use what the mesh around you uses."}),
    fld("Preset", preset), warn, fld("Frequency (MHz)", freq), fld("Bandwidth", bw), fld("Spreading factor", sf), fld("Coding rate", cr),
    fld(`Transmit power (dBm, 1 to ${m.max_tx_power || 22})`, power, "More power reaches further and uses more battery. The antenna matters more."),
    mk("div", {className: "btns"}, save), msg,
    mk("div", {className: "hint", textContent: "Never transmit without an antenna attached: it can damage the radio."}));
};

// ---- settings: channels -------------------------------------------------------------------------------------
const secChannels = () => {
  if (!S.status.connected) return needNode();
  const max = S.me.max_channels || 8, used = new Set(S.channels.map(c => c.idx));
  const free = range(0, max - 1).filter(i => !used.has(i));
  let edit = null;
  const slot = mk("select"), name = mk("input", {type: "text", maxLength: 31, autocomplete: "off", placeholder: "#hiking, or any name"});
  const secret = mk("input", {type: "text", autocomplete: "off", spellcheck: false, placeholder: "32 hex characters, or leave blank"});
  const msg = mk("div", {className: "err"}), title = mk("h3"), go = button("Add channel", null, "act"), cancel = button("Cancel", () => setEdit(null));
  const derived = () => { const h = name.value.startsWith("#"); secret.disabled = h; if (h) secret.value = ""; secret.placeholder = h ? "Worked out from the name" : "32 hex characters, or leave blank"; };
  const setEdit = c => {
    edit = c;
    slot.replaceChildren(...(c ? [c.idx] : free).map(i => mk("option", {value: i, textContent: "Slot " + i})));
    name.value = c ? c.name : ""; secret.value = c && !c.name.startsWith("#") ? c.secret : ""; derived();
    title.textContent = c ? `Edit ${c.name}` : "Add a channel"; go.textContent = c ? "Save" : "Add channel"; cancel.hidden = !c; slot.disabled = !!c;
    go.disabled = !c && !free.length;
  };
  name.addEventListener("input", derived);
  go.onclick = () => withBusy(go, async () => {
    const r = await call("/api/channel/set", {idx: Number(slot.value), name: name.value, secret: secret.value});
    say(msg, r, edit ? "Saved." : "Added."); if (r.ok) { editing = false; setEdit(null); }
  });
  const list = mk("div");
  for (const c of S.channels) {
    list.append(mk("div", {className: "chrow"}, mk("span", {className: "chidx", textContent: c.idx}), mk("b", {textContent: c.name}), mk("span", {className: "grow"}),
      button("Share", () => info(`Channel ${c.name}`, c.name.startsWith("#") ? `Channel: ${c.name}` : `Channel: ${c.name}\nSecret: ${c.secret}`,
        c.name.startsWith("#") ? "Anyone who adds a channel with exactly this name joins it: there is no secret to pass on." : "Give both lines to the person joining. Anyone with the secret can read and post.")),
      button("Edit", () => { setEdit(c); editing = true; }),
      ...(c.idx === 0 ? [] : [button("Remove", async () => { if (confirm(`Remove ${c.name}? Its history stays here, but the node stops hearing it.`)) { const r = await call("/api/channel/remove", {idx: c.idx}); say(msg, r, "Removed."); } }, "ghost danger")])));
  }
  if (!S.channels.length) list.append(mk("div", {className: "hint", textContent: "The node has no channels."}));
  setEdit(null);
  return mk("div", {}, mk("h3", {textContent: "Channels"}), list, title,
    fld("Slot", slot), fld("Name", name, "A name starting with # is public: anyone who types the same name joins. Any other name makes a private channel."),
    fld("Secret", secret, "To join a private channel, enter its secret. Blank makes a new random one."), mk("div", {className: "btns"}, go, cancel), msg);
};

// ---- settings: connection and access -------------------------------------------------------------------------
const secConnection = cfg => {
  const port = mk("select"), baud = mk("select");
  const url = mk("input", {type: "text", value: cfg.values.nomad_url, placeholder: cfg.map_server || "http://localhost:8080", autocomplete: "off", spellcheck: false});
  port.append(mk("option", {value: "", textContent: "Find it automatically" + (cfg.detected.length ? ` (${cfg.detected[0]})` : "")}));
  const ports = new Set([...cfg.ports]); if (cfg.values.serial_port) ports.add(cfg.values.serial_port);
  for (const p of ports) port.append(mk("option", {value: p, textContent: p}));
  port.value = cfg.values.serial_port;
  for (const b of cfg.bauds) baud.append(mk("option", {value: b, textContent: b}));
  baud.value = cfg.values.baud;
  const connMsg = mk("div", {className: "err"});
  const saveConn = button("Save", async () => { const r = await call("/api/settings", {serial_port: port.value, baud: Number(baud.value), nomad_url: url.value}); say(connMsg, r, "Saved."); }, "act");
  const cur = mk("input", {type: "password", autocomplete: "current-password"}), nw = mk("input", {type: "password", autocomplete: "new-password"});
  const accMsg = mk("div", {className: "err"});
  const certBox = location.protocol !== "https:" ? [] : [
    mk("h3", {textContent: "Certificate"}),
    mk("div", {className: "hint", textContent: "This page is served with a certificate the server made for itself, which is why browsers warn about it. Install it on a device and that device stops warning and lets the page send notifications and be installed as an app. Android: Settings > Security > Install a certificate > CA certificate. iPhone: open the file, install the profile, then turn it on under Settings > General > About > Certificate Trust Settings. Computer: open the file and add it to the trusted certificates."}),
    mk("div", {className: "btns"}, button("Download certificate", () => { location.href = "/api/certificate"; }))];
  return mk("div", {}, mk("h3", {textContent: "Connection"}),
    fld("Serial port", port, "Where the node is plugged in. The Pi's own UART pins (/dev/serial0) are only listed here, never guessed."),
    fld("Baud rate", baud), fld("Map server", url, "Project N.O.M.A.D., e.g. http://localhost:8080. Blank: no map, just the node list."),
    mk("div", {className: "btns"}, saveConn), connMsg,
    mk("h3", {textContent: "Access"}), fld("Current password", cur), fld("New password (8 or more characters)", nw),
    mk("div", {className: "btns"},
      button("Change password", async () => { const r = await call("/api/account/password", {current: cur.value, new: nw.value}); say(accMsg, r, "Changed."); if (r.ok) { cur.value = ""; nw.value = ""; } }),
      button("Log out everywhere", async () => { await call("/api/account/logout-all", {}); toLogin(); }, "ghost danger")), accMsg,
    ...certBox);
};

// ---- settings: alerts ---------------------------------------------------------------------------------------
// The choices are this browser's own (see mesh.js): a phone and a computer want different things, so nothing here is saved on the server.
const secAlerts = () => {
  const check = (label, key) => {
    const cb = mk("input", {type: "checkbox", checked: !!prefs[key], onchange: async () => { setPref(key, cb.checked); if (key === "notify" && cb.checked) await askNotifications(); }});
    return mk("div", {className: "fld"}, mk("label", {}, cb, " " + label));
  };
  const why = prefs.notify ? notifyProblem() : "";
  const perm = !("Notification" in window) ? "not available" : Notification.permission === "default" ? "not asked yet" : Notification.permission;
  const secure = location.protocol === "https:";
  return mk("div", {}, mk("h3", {textContent: "Alerts"}),
    mk("div", {className: "hint", textContent: "These apply to this browser only. A card on the map shows for each message that arrives; the Alerts button at the top adds a chime, a vibration and a notification."}),
    check("Channel messages", "channels"), check("Direct messages", "direct"), check("Show a card on the map", "card"),
    check("Play a chime", "chime"), check("Vibrate", "vibrate"), check("Send a notification when this page isn't in front", "notify"),
    mk("div", {className: "hint", textContent: `Notifications: ${perm}.${why ? " Off because " + why + (secure ? "." : " (this page is on plain http)."): ""}`}),
    mk("div", {className: "btns"}, button("Send a test", () => {
      const m = {kind: "dm", from: "Test", peer: "", text: "This is what an alert looks like."};
      if (prefs.card) showCard(m, describe(m));
      if (prefs.chime) chime();
      if (prefs.vibrate) navigator.vibrate?.([200, 100, 200]);
      if (prefs.notify) notify("Test", m.text, "test", true);  // shown even with this page in front, so you can see what it looks like
    })),
    mk("h3", {textContent: "On a phone"}),
    mk("div", {className: "hint", textContent: "Install this page as an app and its notifications come through the phone's own notification system. Android: the browser menu, Install app. iPhone: Share, Add to Home Screen, then open it from the home screen (notifications there need iOS 16.4 or later). Both need the page open over https with the certificate installed (Connection). While the app is closed, nothing arrives: that would need push notifications from a server on the internet."}));
};

// ---- settings: firmware -------------------------------------------------------------------------------------
const FLASHING = ["starting", "downloading", "flashing"];
const updateFlash = () => {
  if (!fw.box || !fw.box.isConnected) return;
  const f = S.flash || {}, running = FLASHING.includes(f.state);
  fw.status.textContent = f.text || "";
  fw.status.className = f.state === "failed" ? "err" : f.state === "done" ? "ok" : "hint";
  fw.progress.hidden = !(running || f.state === "done");
  fw.fill.style.width = (f.percent || 0) + "%";
  fw.log.textContent = (f.log || []).join("\n");
  fw.logwrap.hidden = !(f.log || []).length;
  for (const b of fw.buttons) b.disabled = running || (b.needs && !b.needs());
};
const secFirmware = () => {
  const m = S.me, box = mk("div");
  fw.box = box; fw.buttons = [];
  const btn = (text, fn, needs, cls = "ghost") => { const b = button(text, fn, cls); b.needs = needs; fw.buttons.push(b); return b; };
  const board = S.status.connected && m.model ? `${m.model} running ${m.version || "unknown firmware"}` : "No node answering: pick the firmware for your board yourself.";
  fw.status = mk("div", {className: "hint"});
  fw.fill = mk("i"); fw.progress = mk("div", {className: "bar"}, fw.fill); fw.progress.hidden = true;
  fw.log = mk("pre", {className: "console"}); fw.logwrap = mk("details", {}, mk("summary", {textContent: "Details"}), fw.log); fw.logwrap.hidden = true;
  const relBox = mk("div"), checkMsg = mk("div", {className: "err"});

  const install = async (path, body, label) => {
    const where = S.status.port || "the serial port";
    if (!confirm(`Install ${label} on the node at ${where}?\n\nThe node disconnects for a minute or so. Don't unplug it. Your key, name and channels normally survive an update, but back the key up first (Node section) if the identity matters.`)) return;
    const r = await call(path, body);
    if (!r.ok) { checkMsg.className = "err"; checkMsg.textContent = r.body.error || "Couldn't start."; }
  };
  const showRelease = rel => {
    const pick = sel([["", "Choose your board..."], ...rel.images.map(i => [i.name, i.board])], rel.suggested || "");
    const erase = mk("input", {type: "checkbox"});
    const go = btn("Install", () => install("/api/firmware/flash", {image: pick.value, erase: erase.checked}, rel.images.find(i => i.name === pick.value).board + " " + rel.version), () => !!pick.value, "act");
    pick.onchange = updateFlash;
    const verdict = rel.newer === true ? `Update available: ${rel.version} (you have ${rel.current}).` : rel.newer === false ? `You are up to date (${rel.current}). You can still reinstall ${rel.version}.` : `Latest is ${rel.version}.`;
    relBox.replaceChildren(mk("div", {className: rel.newer ? "ok" : "hint", textContent: verdict}),
      fld("Firmware for", pick, rel.suggested ? "Matched to the node's model." : "The node's model isn't clearly one of these: choose carefully. The wrong board's firmware won't run (it can be reflashed, holding BOOT while plugging in)."),
      mk("label", {className: "fld"}, erase, " Erase everything first (a fresh node: new identity, no channels or contacts)"), mk("div", {className: "btns"}, go));
    updateFlash();
  };
  const check = btn("Check for updates", () => withBusy(check, async () => {
    checkMsg.textContent = ""; const r = await call("/api/firmware/check", {});
    if (!r.ok) { checkMsg.className = "err"; checkMsg.textContent = r.body.error || "Couldn't check."; return; }
    S.release = r.body.release; showRelease(S.release);
  }));
  const file = mk("input", {type: "file", accept: ".bin"});
  const upload = btn("Install file", () => withBusy(upload, async () => {
    const f = file.files[0]; if (!f) return;
    if (f.size > 4 * 1024 * 1024) { checkMsg.className = "err"; checkMsg.textContent = "That file is too big for a firmware image."; return; }
    const bytes = new Uint8Array(await f.arrayBuffer());
    let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    await install("/api/firmware/upload", {name: f.name, data: btoa(bin), erase: false}, f.name);
  }), () => !!file.files.length);
  file.onchange = updateFlash;
  box.append(mk("h3", {textContent: "Firmware"}), kv([["Node", board]]),
    mk("div", {className: "hint", textContent: "Updates ESP32 boards (Heltec, LilyGO, XIAO ESP32 and similar) with MeshCore's companion (USB) firmware. Boards with an nRF52 chip (RAK4631, T114...) need their own updater."}),
    mk("div", {className: "btns"}, check), checkMsg, relBox,
    mk("h3", {textContent: "Or use a file"}), fld("A '-merged.bin' firmware file from this computer", file, "For when this machine has no internet: download it elsewhere, then pick it here."),
    mk("div", {className: "btns"}, upload), mk("h3", {textContent: "Progress"}), fw.status, fw.progress, fw.logwrap);
  if (S.release) showRelease(S.release);
  return box;
};

// ---- the admin dialog: another node's side ---------------------------------------------------------------------
const hopList = (hex, size) => { const w = size * 2, out = []; for (let i = 0; i + w <= hex.length; i += w) out.push(hopName(hex.slice(i, i + w))); return out; };
const nameByPrefix = p => { const n = [...S.nodes.values()].find(x => x.key.startsWith(p)); return n ? n.name : p; };
const routeText = r => {
  const to = r.out_path_len ? hopList(r.out_path, r.out_path_hash_len).join(" > ") : "direct";
  const back = r.in_path_len ? hopList(r.in_path, r.in_path_hash_len).join(" > ") : "direct";
  return `Route there: ${to}. Route back: ${back}.`;
};
let adminKey = null;
const openAdmin = key => {
  const n = S.nodes.get(key); if (!n) return;
  adminKey = key;
  const body = $("admin-body"), btns = [];
  $("admin-title").textContent = n.name;
  const out = mk("div", {className: "adminout"}), who = mk("span", {className: "hint"}), logout = mk("button", {type: "button", className: "ghost", textContent: "Log out"});
  const pw = mk("input", {type: "password", autocomplete: "off", placeholder: "Password (blank for guest)"});
  const setBusy = on => { for (const b of btns) b.disabled = on; logout.disabled = on; };
  const ask = async (path, extra = {}, wait = "Asking over the mesh. This can take up to a minute...") => {
    out.replaceChildren(mk("div", {className: "hint", textContent: wait})); setBusy(true);
    const r = await call(path, {key, ...extra}); setBusy(false);
    if (!r.ok) out.replaceChildren(mk("div", {className: "err", textContent: r.body.error || "Failed."}));
    return r.ok ? r.body : null;
  };
  const tool = (text, fn) => { const b = mk("button", {type: "button", className: "ghost", textContent: text, onclick: async () => { const r = await fn(); if (r) out.replaceChildren(r); }}); btns.push(b); return b; };
  const loggedIn = s => { who.textContent = s.logged_in ? (s.admin ? "Logged in as admin" : "Logged in as guest") : "Not logged in"; logout.hidden = !s.logged_in; };
  const loginBtn = mk("button", {type: "button", className: "act", textContent: "Log in", onclick: async () => {
    const r = await ask("/api/admin/login", {password: pw.value}, "Logging in over the mesh..."); if (!r) return;
    pw.value = ""; loggedIn(r); out.replaceChildren(mk("div", {className: "ok", textContent: r.admin ? "Logged in with admin rights." : "Logged in as a guest."}));
  }}); btns.push(loginBtn);
  logout.onclick = async () => { await ask("/api/admin/logout"); loggedIn({logged_in: false}); out.replaceChildren(); };
  pw.onkeydown = e => { if (e.key === "Enter") loginBtn.click(); };

  const status = tool("Status", async () => { const b = await ask("/api/admin/status"); if (!b) return null; const s = b.status;
    return kv([["Battery", (s.bat / 1000).toFixed(2) + " V"], ["Uptime", dur(s.uptime)], ["Heard / sent", `${s.nb_recv} / ${s.nb_sent}`],
      ["Last signal", `${s.last_snr} dB SNR, ${s.last_rssi} dBm`], ["Noise floor", s.noise_floor + " dBm"], ["Airtime", dur(s.airtime) + " sending"],
      ["Flood / direct sent", `${s.sent_flood} / ${s.sent_direct}`], ["Flood / direct heard", `${s.recv_flood} / ${s.recv_direct}`],
      ["Duplicates dropped", `${s.flood_dups} flood, ${s.direct_dups} direct`], ["Queue", s.tx_queue_len]]); });
  const neigh = tool("Neighbours", async () => { const b = await ask("/api/admin/neighbours"); if (!b) return null; const list = b.neighbours.neighbours || [];
    if (!list.length) return mk("div", {className: "hint", textContent: "It hears no other nodes directly."});
    return mk("table", {}, mk("tbody", {}, ...list.map(x => mk("tr", {}, mk("td", {textContent: nameByPrefix(x.pubkey)}), mk("td", {textContent: ago(now() - x.secs_ago)}), mk("td", {textContent: x.snr + " dB"}))))); });
  const tele = tool("Telemetry", async () => { const b = await ask("/api/admin/telemetry"); if (!b) return null;
    return b.telemetry.length ? kv(b.telemetry.map(t => [`${t.type} (ch ${t.channel})`, typeof t.value === "object" ? JSON.stringify(t.value) : t.value])) : mk("div", {className: "hint", textContent: "No readings."}); });
  const acl = tool("Access list", async () => { const b = await ask("/api/admin/acl"); if (!b) return null;
    return b.acl.length ? kv(b.acl.map(a => [nameByPrefix(a.key), "permission " + a.perm])) : mk("div", {className: "hint", textContent: "The list is empty."}); });
  const route = tool("Find route", async () => { const b = await ask("/api/admin/route", {}, "Finding the route..."); return b ? mk("div", {textContent: routeText(b.route)}) : null; });

  // the command line
  const log = mk("div", {className: "console"}), cmd = mk("input", {type: "text", placeholder: "Command, e.g. ver", autocomplete: "off", spellcheck: false});
  const line = (text, cls) => { log.append(mk("div", {className: cls, textContent: text})); log.scrollTop = log.scrollHeight; };
  const send = mk("button", {type: "button", className: "act", textContent: "Send"}); btns.push(send);
  const run = async text => {
    if (!text.trim()) return; line("> " + text, "cmdline"); cmd.value = ""; setBusy(true);
    const r = await call("/api/admin/command", {key, text}); setBusy(false);
    line(r.ok ? r.body.reply : r.body.error || "Failed.", r.ok ? "reply" : "bad");
  };
  send.onclick = () => run(cmd.value); cmd.onkeydown = e => { if (e.key === "Enter") run(cmd.value); };
  const chips = ["ver", "clock", "get name", "get radio", "advert"].map(c => { const b = mk("button", {type: "button", className: "ghost small", textContent: c, onclick: () => run(c)}); btns.push(b); return b; });

  const open = n.type === 3 ? mk("button", {type: "button", className: "ghost", textContent: "Open chat", onclick: () => { $("admin").close(); openDm(n.key.slice(0, 12)); }}) : null;
  body.replaceChildren(
    mk("div", {className: "hint", textContent: (TYPE[n.type] || "Node") + (n.hops != null && n.hops >= 0 ? (n.hops === 0 ? ", direct" : `, ${n.hops} hop${n.hops === 1 ? "" : "s"} away`) : ", route not yet known") + ". Everything here goes over the radio, so it is slow and can fail if the node is out of range."}),
    mk("div", {className: "row"}, pw, loginBtn), mk("div", {className: "row", style: "margin:4px 0 8px"}, who, mk("span", {className: "grow"}), logout),
    mk("div", {className: "btns"}, status, neigh, tele, acl, route, ...(open ? [open] : [])), out,
    mk("h3", {textContent: "Command line"}), log, mk("div", {className: "row", style: "margin-top:6px"}, cmd, send), mk("div", {className: "btns", style: "margin-top:6px"}, ...chips),
    mk("div", {className: "hint", textContent: "Most commands need an admin login. The node answers with one line, or not at all (reboot never answers)."}));
  loggedIn({logged_in: false}); logout.hidden = true;
  call("/api/admin/state", {key}).then(r => { if (adminKey === key && r.ok) loggedIn(r.body); });
  if (!$("admin").open) $("admin").showModal();
};
$("admin-close").onclick = () => $("admin").close();
$("admin").addEventListener("close", () => { adminKey = null; });

maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
const note = t => document.getElementById("msg").textContent = t;
const blank = {version: 8, sources: {}, layers: [{id: "bg", type: "background", paint: {"background-color": "#e8e4d8"}}]};
const map = new maplibregl.Map({container: "map", style: blank, center: [-98, 39], zoom: 3});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl());
map.on("error", e => console.log("map error: " + (e.error && e.error.message)));
fetch("/style.json").then(r => r.ok ? r.json() : Promise.reject())
  .then(s => map.setStyle(s, {diff: false})).then(() => note(""))
  .catch(() => note("Map server unreachable - showing stations only"));

// What a popup button does is up to the page around the map: the web page defines window.onStationAction;
// the desktop window doesn't, so it gets a navigation that its Qt side intercepts (see MapPage in app.py).
const act = (what, call) => {
  if (typeof window.onStationAction === "function") window.onStationAction(what, call);
  else location.href = "simpleaprs://" + what + "/?call=" + encodeURIComponent(call);
};

// Packet contents come off the air, so build the popup with textContent only.
const popup = (call, info, me) => {
  const box = document.createElement("div");
  const h = document.createElement("h4"); h.textContent = call; box.appendChild(h);
  const dl = document.createElement("dl");
  for (const [k, v] of info || []) {
    if (!v) continue;
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k;
    if (k === "Raw") { const c = document.createElement("code"); c.textContent = v; dd.appendChild(c); }
    else dd.textContent = v;
    dl.append(dt, dd);
  }
  box.appendChild(dl);
  // You can't message yourself, and an object's name isn't a callsign anything listens on.
  if (!me && !(info || []).some(([k, v]) => k === "Sent by" && v)) {
    const b = document.createElement("button");
    b.textContent = "Message"; b.onclick = () => act("message", call);
    box.appendChild(b);
  }
  return box;
};

const glyph = icon => '<svg ' + SVG_ATTRS + '>' + (GLYPHS[icon] || GLYPHS.house) + "</svg>";

// Route of the clicked station: dashed segments cross hops we have no position for.
let routeData = {type: "FeatureCollection", features: []};
const drawRoute = segs => {
  routeData = {type: "FeatureCollection", features: (segs || []).map(s => (
    {type: "Feature", properties: {dashed: s.dashed}, geometry: {type: "LineString", coordinates: s.coords}}))};
  const src = map.getSource("route");
  if (src) src.setData(routeData);
};
// Tracks: where a moving station has been, one green line per station.
let trailData = {type: "FeatureCollection", features: []};
const markers = {};
const drawTrails = () => {
  trailData = {type: "FeatureCollection", features: Object.values(markers)
    .filter(m => m.trail.length >= 2 && m.getElement().style.display !== "none")
    .map(m => ({type: "Feature", properties: {old: m.getElement().classList.contains("old")},
                geometry: {type: "LineString", coordinates: m.trail.map(([lat, lon]) => [lon, lat])}}))};
  const src = map.getSource("trails");
  if (src) src.setData(trailData);
};
// Everything setStation changes is redrawn once per frame, not once per station: a backlog is thousands of calls.
let refreshQueued = false;
const refresh = () => {
  if (refreshQueued) return;
  refreshQueued = true;
  requestAnimationFrame(() => {
    refreshQueued = false;
    drawTrails();
    const n = Object.values(markers).filter(m => m.getElement().classList.contains("old")).length;
    clearOld.hidden = n === 0;
    clearOld.textContent = `Clear inactive (${n})`;
  });
};
map.on("style.load", () => {  // setStyle drops sources, so add ours again each time
  map.addSource("trails", {type: "geojson", data: trailData});
  map.addLayer({id: "trails", type: "line", source: "trails", layout: {"line-cap": "round", "line-join": "round"},
    paint: {"line-color": "#1f9d3a", "line-width": 3, "line-opacity": ["case", ["get", "old"], 0.35, 0.85]}});
  map.addSource("route", {type: "geojson", data: routeData});
  const paint = {"line-color": "#6a2fd6", "line-width": 3, "line-opacity": 0.85};
  map.addLayer({id: "route-solid", type: "line", source: "route", filter: ["!", ["get", "dashed"]], paint});
  map.addLayer({id: "route-dashed", type: "line", source: "route", filter: ["get", "dashed"],
    paint: {...paint, "line-dasharray": [2, 2]}});
});

let shown = null;  // call whose route is on the map
let directSegs = [];  // lines from your own station to each station heard straight from it, drawn when your own icon is clicked

// Each click on a station steps it through: popup + route -> route only -> nothing.
const clearStation = call => {
  const m = markers[call];
  m.state = null;
  m.pop.remove();
  if (shown === call) { shown = null; drawRoute([]); }
};
// Bring a station and everything its lines reach into view (never zooming in further than maxZoom).
const fitTo = (m, segs, maxZoom) => {
  const here = m.getLngLat(), box = new maplibregl.LngLatBounds(here, here);
  for (const s of segs) for (const c of s.coords) box.extend(c);
  if (segs.length) map.fitBounds(box, {padding: 70, maxZoom});
  else map.flyTo({center: here, zoom: maxZoom});
};
const clickStation = call => {
  const m = markers[call];
  if (shown && shown !== call) clearStation(shown);  // one route at a time
  if (m.state === "popup") { m.state = "route"; m.pop.remove(); }
  else if (m.state === "route") clearStation(call);
  else {
    m.state = "popup"; shown = call;
    m.pop.setLngLat(m.getLngLat()).addTo(map);
    drawRoute(m.isMe ? directSegs : m.routeSegs);
    if (m.isMe && directSegs.length) fitTo(m, directSegs, map.getZoom());  // the stations you hear may be off the screen: zoom out to them, never in
  }
};
// Show a station as a click on it would (popup and route), and bring it into view with its route.
window.showStation = (call, zoom) => {
  const m = markers[call];
  if (!m) return;
  if (shown && shown !== call) clearStation(shown);
  shown = call;
  if (m.state !== "popup") { m.state = "popup"; m.pop.setLngLat(m.getLngLat()).addTo(map); }
  const segs = m.isMe ? directSegs : m.routeSegs;
  drawRoute(segs);
  fitTo(m, segs, zoom || map.getZoom());
};
// The stations your own station hears directly, as [lon, lat] points to draw lines to (the page works that out).
window.setDirect = (from, tos) => {
  directSegs = tos.map(to => ({dashed: false, coords: [from, to]}));
  if (shown && markers[shown]?.isMe) drawRoute(directSegs);
};
// info: [[label, value], ...] shown when the marker is clicked; icon/cls pick the badge shape and colour
window.setStation = (call, lat, lon, label, me, info, stale, icon, cls, segs, visible, trail) => {
  let m = markers[call];
  if (!m) {
    const el = document.createElement("div");
    el.innerHTML = '<span class="ic"></span><b></b>';
    m = markers[call] = new maplibregl.Marker({element: el}).setLngLat([lon, lat]).addTo(map);
    m.pop = new maplibregl.Popup({offset: 16, closeButton: true});
    // Closing the popup with its X leaves the route up; a click on the map clears both (see below).
    m.pop.on("close", () => { if (m.state === "popup") m.state = "route"; });
    el.addEventListener("click", ev => { ev.stopPropagation(); clickStation(call); });
  }
  const el = m.getElement();
  // classList, not className: MapLibre keeps its own positioning class on this element
  el.classList.add("st");
  for (const c of ["wx", "igate", "digi", "me", "other"]) el.classList.toggle(c, c === (me ? "me" : cls || "other"));
  el.classList.toggle("old", !!stale);
  el.querySelector(".ic").innerHTML = glyph(icon);
  m.setLngLat([lon, lat]);
  el.querySelector("b").textContent = label;
  m.pop.setDOMContent(popup(call, info, me));
  m.isMe = !!me;
  m.routeSegs = segs || [];
  m.trail = trail || [];
  el.style.display = visible === false ? "none" : "";
  if (visible === false && m.state) clearStation(call);
  if (shown === call) drawRoute(m.isMe ? directSegs : m.routeSegs);  // a hop's position may have just been learned
  if (m.state === "popup") m.pop.setLngLat([lon, lat]);
  refresh();
};
window.removeStations = calls => {
  for (const call of calls) {
    const m = markers[call];
    if (!m) continue;
    if (shown === call) { shown = null; drawRoute([]); }
    m.pop.remove();
    m.remove();
    delete markers[call];
  }
  refresh();
};
// A click anywhere else on the map clears the route (and popup) being shown.
map.on("click", () => { if (shown) clearStation(shown); });
window.centerOn = (lat, lon, zoom) => map.flyTo({center: [lon, lat], zoom: zoom || map.getZoom()});

// What the colours mean, and the one button that acts on them. Built here so the desktop window and the web page both get it.
const legend = document.createElement("details");
legend.id = "legend";
const rows = [
  ["dot", "#d33", "Station"], ["dot", "#1e6fe8", "Weather station"], ["dot", "#1f9d3a", "iGate"],
  ["dot", "#e08a00", "Digipeater"], ["dot", "#8a3ffc", "You (your radio)"], ["dot old", "#d33", "Not heard since start"],
  ["line", "#1f9d3a", "Track of a moving station"], ["line", "#6a2fd6", "Route of the clicked station"], ["line", "#6a2fd6", "Heard directly (click your own)"],
  ["line dashed", "#6a2fd6", "Hop with no known position"]];
legend.innerHTML = "<summary>Legend</summary>" + rows.map(([k, c, t]) => `<div><i class="${k}" style="--c:${c}"></i>${t}</div>`).join("");
const clearOld = document.createElement("button");
clearOld.hidden = true;
clearOld.textContent = "Clear inactive (0)";
clearOld.title = "Forget the stations that are shown grey";
clearOld.onclick = () => act("clear-inactive", "");
legend.appendChild(clearOld);
map.getContainer().appendChild(legend);

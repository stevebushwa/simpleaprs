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
map.on("style.load", () => {  // setStyle drops sources, so add ours again each time
  map.addSource("route", {type: "geojson", data: routeData});
  const paint = {"line-color": "#6a2fd6", "line-width": 3, "line-opacity": 0.85};
  map.addLayer({id: "route-solid", type: "line", source: "route", filter: ["!", ["get", "dashed"]], paint});
  map.addLayer({id: "route-dashed", type: "line", source: "route", filter: ["get", "dashed"],
    paint: {...paint, "line-dasharray": [2, 2]}});
});

const markers = {};
let shown = null;  // call whose route is on the map

// Each click on a station steps it through: popup + route -> route only -> nothing.
const clearStation = call => {
  const m = markers[call];
  m.state = null;
  m.pop.remove();
  if (shown === call) { shown = null; drawRoute([]); }
};
const clickStation = call => {
  const m = markers[call];
  if (shown && shown !== call) clearStation(shown);  // one route at a time
  if (m.state === "popup") { m.state = "route"; m.pop.remove(); }
  else if (m.state === "route") clearStation(call);
  else {
    m.state = "popup"; shown = call;
    m.pop.setLngLat(m.getLngLat()).addTo(map);
    drawRoute(m.routeSegs);
  }
};
// info: [[label, value], ...] shown when the marker is clicked; icon/cls pick the badge shape and colour
window.setStation = (call, lat, lon, label, me, info, stale, icon, cls, segs, visible) => {
  let m = markers[call];
  if (!m) {
    const el = document.createElement("div");
    el.innerHTML = '<span class="ic"></span><b></b>';
    m = markers[call] = new maplibregl.Marker({element: el}).setLngLat([lon, lat]).addTo(map);
    m.pop = new maplibregl.Popup({offset: 16, closeButton: true});
    // Closing the popup (its X, or a click on the map) leaves the route up.
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
  m.routeSegs = segs || [];
  el.style.display = visible === false ? "none" : "";
  if (visible === false && m.state) clearStation(call);
  if (shown === call) drawRoute(m.routeSegs);  // a hop's position may have just been learned
  if (m.state === "popup") m.pop.setLngLat([lon, lat]);
};
window.centerOn = (lat, lon, zoom) => map.flyTo({center: [lon, lat], zoom: zoom || map.getZoom()});

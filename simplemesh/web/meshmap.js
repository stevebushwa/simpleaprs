// The map: MapLibre on the offline vector tiles N.O.M.A.D. serves (through this server's proxy), with a badge for each
// node and a line for the route to the one you clicked. The page around it (mesh.js) decides what a click does;
// everything here is drawing.
maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
const blank = {version: 8, sources: {}, layers: [{id: "bg", type: "background", paint: {"background-color": "#e8e4d8"}}]};
const map = new maplibregl.Map({container: "map", style: blank, center: [-98, 39], zoom: 3});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl());
map.on("error", e => console.log("map error: " + (e.error && e.error.message)));

const note = t => { document.getElementById("msg").textContent = t; };
fetch("/style.json").then(r => r.ok ? r.json() : Promise.reject())
  .then(s => map.setStyle(s, {diff: false})).then(() => note(""))
  .catch(() => note("Map server unreachable - showing nodes only"));

const GLYPH = {
  1: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-4 3-6 7-6s7 2 7 6"/>',
  2: '<path d="M12 11v10M8 21h8"/><circle cx="12" cy="9" r="1.6"/><path d="M7.5 5.5a6.4 6.4 0 0 0 0 7M16.5 5.5a6.4 6.4 0 0 1 0 7"/>',
  3: '<path d="M4 5h16v11H10l-5 4v-4H4z"/>',
  4: '<path d="M5 17a8 8 0 1 1 14 0"/><path d="M12 13l4-4"/>',
  0: '<circle cx="12" cy="12" r="5"/>',
  me: '<path d="M12 3l2.6 6 6.4.6-4.8 4.3 1.4 6.3L12 17l-5.6 3.2 1.4-6.3L3 9.6 9.4 9z"/>',
};
const glyph = t => '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (GLYPH[t] || GLYPH[0]) + "</svg>";

const markers = {};
let me = null;        // marker for our own node
let shown = null;     // key whose popup and route are up
let routeData = {type: "FeatureCollection", features: []};

map.on("style.load", () => {  // setStyle drops sources, so add ours again each time
  map.addSource("route", {type: "geojson", data: routeData});
  const paint = {"line-color": "#6a2fd6", "line-width": 3, "line-opacity": 0.85};
  map.addLayer({id: "route-solid", type: "line", source: "route", filter: ["!", ["get", "dashed"]], paint});
  map.addLayer({id: "route-dashed", type: "line", source: "route", filter: ["get", "dashed"], paint: {...paint, "line-dasharray": [2, 2]}});
});

const MeshMap = window.MeshMap = {
  popupFor: key => null,   // set by the page: the popup's DOM for a node
  ready: typeof maplibregl !== "undefined",

  drawRoute(segs) {
    routeData = {type: "FeatureCollection", features: (segs || []).map(s => (
      {type: "Feature", properties: {dashed: s.dashed}, geometry: {type: "LineString", coordinates: s.coords}}))};
    const src = map.getSource("route");
    if (src) src.setData(routeData);
  },

  close() {
    if (!shown) return;
    const m = markers[shown];
    shown = null;
    if (m) m.pop.remove();
    MeshMap.drawRoute([]);
    MeshMap.onselect(null);
  },
  onselect: key => {},

  open(key) {
    const m = markers[key];
    if (!m) return;
    if (shown && shown !== key) MeshMap.close();
    shown = key;
    m.pop.setDOMContent(MeshMap.popupFor(key)).setLngLat(m.getLngLat()).addTo(map);
    MeshMap.onselect(key);
  },

  // n: node; opts: {visible, old}
  setNode(n, opts) {
    if (n.lat == null || n.lon == null) return MeshMap.remove([n.key]);
    let m = markers[n.key];
    if (!m) {
      const el = document.createElement("div");
      el.innerHTML = '<span class="ic"></span><b></b>';
      m = markers[n.key] = new maplibregl.Marker({element: el}).setLngLat([n.lon, n.lat]).addTo(map);
      m.pop = new maplibregl.Popup({offset: 18, closeButton: true});
      m.pop.on("close", () => { if (shown === n.key) { shown = null; MeshMap.drawRoute([]); MeshMap.onselect(null); } });
      el.addEventListener("click", ev => { ev.stopPropagation(); shown === n.key ? MeshMap.close() : MeshMap.open(n.key); });
    }
    const el = m.getElement();
    el.className = "mk maplibregl-marker t" + (n.type || 0) + (n.saved ? "" : " unsaved") + (opts.old ? " old" : "");
    el.querySelector(".ic").innerHTML = glyph(n.type);
    el.querySelector("b").textContent = n.name;
    m.setLngLat([n.lon, n.lat]);
    el.style.display = opts.visible ? "" : "none";
    if (!opts.visible && shown === n.key) MeshMap.close();
    if (shown === n.key) m.pop.setDOMContent(MeshMap.popupFor(n.key)).setLngLat([n.lon, n.lat]);
  },

  remove(keys) {
    for (const k of keys) {
      const m = markers[k];
      if (!m) continue;
      if (shown === k) MeshMap.close();
      m.pop.remove(); m.remove(); delete markers[k];
    }
  },

  setMe(lat, lon, name) {
    if (lat == null || lon == null) { if (me) { me.remove(); me = null; } return; }
    if (!me) {
      const el = document.createElement("div");
      el.innerHTML = '<span class="ic"></span><b></b>';
      me = new maplibregl.Marker({element: el}).setLngLat([lon, lat]).addTo(map);
    }
    const el = me.getElement();
    el.className = "mk maplibregl-marker me";
    el.querySelector(".ic").innerHTML = glyph("me");
    el.querySelector("b").textContent = name || "This node";
    me.setLngLat([lon, lat]);
  },

  flyTo: (lat, lon, zoom) => map.flyTo({center: [lon, lat], zoom: zoom || Math.max(map.getZoom(), 11)}),
  center: () => map.getCenter(),
  fit(points) {  // points: [[lat, lon], ...]
    if (!points.length) return;
    if (points.length === 1) return map.jumpTo({center: [points[0][1], points[0][0]], zoom: 11});
    const b = new maplibregl.LngLatBounds();
    for (const [la, lo] of points) b.extend([lo, la]);
    map.fitBounds(b, {padding: 60, maxZoom: 13, duration: 0});
  },
  jump: (lat, lon, zoom) => map.jumpTo({center: [lon, lat], zoom}),
  resize: () => map.resize(),
};
map.on("click", () => MeshMap.close());

const legend = document.createElement("details");
legend.id = "legend";
const rows = [["#1e6fe8", "Companion"], ["#e08a00", "Repeater"], ["#8a3ffc", "Room server"], ["#1f9d3a", "Sensor"],
              ["#111", "This node"]];
legend.innerHTML = "<summary>Legend</summary>" + rows.map(([c, t]) => `<div><i class="dot" style="--c:${c}"></i>${t}</div>`).join("") +
  '<div><i class="dot unsaved" style="--c:#777"></i>Heard, not saved</div><div><i class="dot old" style="--c:#777"></i>Not heard lately</div>' +
  '<div><i class="line" style="--c:#6a2fd6"></i>Route of the clicked node</div><div><i class="line dashed" style="--c:#6a2fd6"></i>Hop with no known position</div>';
try { legend.open = localStorage.getItem("legend") !== "closed"; } catch { legend.open = true; }
legend.addEventListener("toggle", () => { try { localStorage.setItem("legend", legend.open ? "open" : "closed"); } catch {} });
map.getContainer().appendChild(legend);

async function loadCsv(path) {
  const text = await fetch(path).then((r) => r.text());
  const raw = text.trim();
  if (!raw) return [];

  const lines = raw.split(/\r?\n/);

  function parseCsvLine(line) {
    const out = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuotes && line[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (ch === "," && !inQuotes) {
        out.push(cur);
        cur = "";
      } else {
        cur += ch;
      }
    }
    out.push(cur);
    return out;
  }

  const cols = parseCsvLine(lines[0]);
  return lines.slice(1).filter(Boolean).map((row) => {
    const values = parseCsvLine(row);
    const obj = {};
    cols.forEach((c, i) => {
      obj[c] = values[i] ?? "";
    });
    return obj;
  });
}

function formatInt(value) {
  return Number(value).toLocaleString();
}

function formatScore(value) {
  return Number(value).toFixed(1);
}

function colorByScore(score) {
  if (score >= 55) return "#7f1d1d";
  if (score >= 45) return "#b91c1c";
  if (score >= 35) return "#ea580c";
  if (score >= 25) return "#d97706";
  if (score >= 15) return "#65a30d";
  return "#2563eb";
}

function buildKpis(summary, table) {
  const kpiRow = document.getElementById("kpiRow");
  const top = table[0];
  const items = [
    { label: "Admin-2 Units Scored", value: formatInt(summary.total_admin2) },
    { label: "Target Beneficiaries", value: formatInt(summary.total_recommended_beneficiaries) },
    { label: "Highest Priority Area", value: `${top.admin2_name}, ${top.admin1_name}` },
    { label: "Top Score", value: formatScore(top.priority_score) }
  ];

  kpiRow.innerHTML = items
    .map(
      (item) => `
      <div class="kpi">
        <p class="kpi__label">${item.label}</p>
        <p class="kpi__value">${item.value}</p>
      </div>
    `
    )
    .join("");
}

function buildIndexComponentTable(rows) {
  const tbody = document.querySelector("#indexComponentsTable tbody");
  tbody.innerHTML = rows
    .map(
      (r) => `
      <tr>
        <td>${r.dimension}</td>
        <td>${r.indicator}</td>
        <td>${Number(r.weight_within_dimension).toFixed(2)}</td>
        <td>${r.source}</td>
      </tr>
    `
    )
    .join("");
}

function buildTopTable(rows) {
  const tbody = document.querySelector("#topTable tbody");
  const top15 = rows.slice(0, 15);
  tbody.innerHTML = top15
    .map(
      (r) => `
      <tr>
        <td>${r.priority_rank}</td>
        <td>${r.admin2_name}</td>
        <td>${r.admin1_name}</td>
        <td>${formatScore(r.priority_score)}</td>
        <td>${formatInt(r.recommended_beneficiaries)}</td>
      </tr>
    `
    )
    .join("");
}

function buildSubareaTable(rows) {
  const tbody = document.querySelector("#subareaTable tbody");
  if (!tbody) return;
  const top20 = rows
    .slice()
    .sort((a, b) => Number(a.subarea_rank) - Number(b.subarea_rank))
    .slice(0, 20);

  tbody.innerHTML = top20
    .map(
      (r) => `
      <tr>
        <td>${formatInt(r.subarea_rank)}</td>
        <td>${r.site_name}</td>
        <td>${r.site_type}</td>
        <td>${formatInt(r.population_near_site)}</td>
        <td>${formatScore(r.local_disaster_score)}</td>
        <td>${Number(r.is_top_location_proxy) === 1 ? "Top proxy" : "Candidate"}</td>
      </tr>
    `
    )
    .join("");
}

function buildNotes(summary) {
  const notes = document.getElementById("tradeoffNotes");
  const addOn = [
    "Tradeoff: IPC and MPI are primarily available at Admin-1 or selected livelihood-zone level, so county-level poverty indicators were propagated to Admin-2 unless a specific subpopulation estimate existed.",
    "Tradeoff: The final weighting (75% disaster, 25% poverty) prioritizes immediate flood impact under rapid-response constraints, while preserving poverty targeting intent.",
    "Allocation method: all 5,000 slots are concentrated in the highest-priority Admin-2; populated-place locality proxies are ranked for decision support rather than automatically splitting recipients."
  ];

  const items = [...summary.assumptions, ...addOn];
  notes.innerHTML = items.map((x) => `<li>${x}</li>`).join("");
}

function addLegend(map) {
  const legend = L.control({ position: "bottomright" });
  legend.onAdd = function () {
    const div = L.DomUtil.create("div", "legend");
    const bins = [
      [55, "#7f1d1d", "55+"],
      [45, "#b91c1c", "45-54.9"],
      [35, "#ea580c", "35-44.9"],
      [25, "#d97706", "25-34.9"],
      [15, "#65a30d", "15-24.9"],
      [0, "#2563eb", "<15"]
    ];
    div.innerHTML = "<strong>Priority Score</strong><br/>" +
      bins.map((b) => `<span class=\"swatch\" style=\"background:${b[1]}\"></span>${b[2]}`).join("<br/>");
    return div;
  };
  legend.addTo(map);
}

async function buildMap(tableRows) {
  const map = L.map("map", { zoomControl: true, minZoom: 5 });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  const geojson = await fetch("data/admin2_priority.geojson").then((r) => r.json());

  const byPcode = new Map(tableRows.map((r) => [r.admin2_pcode, r]));

  const layer = L.geoJSON(geojson, {
    style: (feature) => {
      const pcode = feature.properties.admin2_pcode;
      const row = byPcode.get(pcode);
      const score = row ? Number(row.priority_score) : 0;
      return {
        color: "#1f2937",
        weight: 0.4,
        fillColor: colorByScore(score),
        fillOpacity: 0.72
      };
    },
    onEachFeature: (feature, l) => {
      const row = byPcode.get(feature.properties.admin2_pcode);
      if (!row) return;
      l.bindPopup(
        `<strong>${row.admin2_name}</strong>, ${row.admin1_name}<br/>` +
          `Rank: ${row.priority_rank}<br/>` +
          `Priority score: ${formatScore(row.priority_score)}<br/>` +
          `Disaster score: ${formatScore(Number(row.disaster_score) * 100)}<br/>` +
          `Poverty score: ${formatScore(Number(row.poverty_score) * 100)}<br/>` +
          `Affected pop (proxy): ${formatInt(row.affected_population_proxy)}<br/>` +
          `Recommended beneficiaries: ${formatInt(row.recommended_beneficiaries)}`
      );
    }
  }).addTo(map);

  const homeControl = L.control({ position: "topright" });
  let homeBounds = null;
  homeControl.onAdd = function () {
    const div = L.DomUtil.create("div", "leaflet-bar");
    const btn = L.DomUtil.create("a", "map-home-btn", div);
    btn.href = "#";
    btn.title = "Reset map to initial extent";
    btn.innerText = "Home";
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.on(btn, "click", (e) => {
      L.DomEvent.preventDefault(e);
      if (homeBounds) map.fitBounds(homeBounds, { padding: [20, 20] });
    });
    return div;
  };
  homeControl.addTo(map);

  homeBounds = layer.getBounds().pad(0.05);
  map.fitBounds(homeBounds);
  addLegend(map);
}

async function buildSubareaMap(subRows) {
  const map = L.map("subareaMap", { zoomControl: true, minZoom: 6 });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  const [focalAdm2, focalFlood, subGeo] = await Promise.all([
    fetch("data/focal_adm2.geojson").then((r) => r.json()),
    fetch("data/focal_flood_extent.geojson").then((r) => r.json()),
    fetch("data/focal_subarea_priority.geojson").then((r) => r.json())
  ]);

  const colorBySubScore = (score) => {
    if (score >= 75) return "#991b1b";
    if (score >= 50) return "#c2410c";
    if (score >= 25) return "#ca8a04";
    return "#3f6212";
  };

  const adminLayer = L.geoJSON(focalAdm2, {
    style: {
      color: "#1f2937",
      weight: 2,
      fillColor: "#f59e0b",
      fillOpacity: 0.14
    }
  }).addTo(map);

  const floodLayer = L.geoJSON(focalFlood, {
    style: {
      color: "#0369a1",
      weight: 1,
      fillColor: "#38bdf8",
      fillOpacity: 0.35
    }
  }).addTo(map);

  const pointsLayer = L.geoJSON(subGeo, {
    pointToLayer: (feature, latlng) => {
      const score = Number(feature.properties.subarea_priority_score || 0);
      return L.circleMarker(latlng, {
        radius: 6,
        color: "#111827",
        weight: 1,
        fillColor: colorBySubScore(score),
        fillOpacity: 0.9
      });
    },
    onEachFeature: (feature, l) => {
      const p = feature.properties;
      l.bindPopup(
        `<strong>${p.site_name}</strong><br/>` +
          `Type: ${p.site_type}<br/>` +
          `Rank: ${formatInt(p.subarea_rank)}<br/>` +
          `Nearby pop: ${formatInt(p.population_near_site)}<br/>` +
          `Flood exposure: ${formatScore(p.flood_exposure_pct)}%<br/>` +
          `CHIRPS mean: ${formatScore(p.chirps_near_site_mm)} mm<br/>` +
          `Local disaster score: ${formatScore(p.local_disaster_score)}<br/>` +
          `Proxy status: ${Number(p.is_top_location_proxy) === 1 ? "Top recommended locality" : "Candidate locality"}`
      );
    }
  }).addTo(map);

  const homeControl = L.control({ position: "topright" });
  let homeBounds = null;
  homeControl.onAdd = function () {
    const div = L.DomUtil.create("div", "leaflet-bar");
    const btn = L.DomUtil.create("a", "map-home-btn", div);
    btn.href = "#";
    btn.title = "Reset focal map to initial extent";
    btn.innerText = "Home";
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.on(btn, "click", (e) => {
      L.DomEvent.preventDefault(e);
      if (homeBounds) map.fitBounds(homeBounds, { padding: [20, 20] });
    });
    return div;
  };
  homeControl.addTo(map);

  const group = L.featureGroup([adminLayer, floodLayer, pointsLayer]);
  homeBounds = group.getBounds().pad(0.08);
  map.fitBounds(homeBounds);

  buildSubareaTable(subRows);
}

async function main() {
  const [table, indexComponents, summary, subRows] = await Promise.all([
    loadCsv("data/admin2_priority_table.csv"),
    loadCsv("data/index_components.csv"),
    fetch("data/summary.json").then((r) => r.json()),
    loadCsv("data/focal_subarea_priority.csv")
  ]);

  table.forEach((r) => {
    r.priority_rank = Number(r.priority_rank);
    r.priority_score = Number(r.priority_score);
    r.recommended_beneficiaries = Number(r.recommended_beneficiaries);
  });

  subRows.forEach((r) => {
    r.subarea_rank = Number(r.subarea_rank);
    r.population_near_site = Number(r.population_near_site);
    r.local_disaster_score = Number(r.local_disaster_score);
    r.is_top_location_proxy = Number(r.is_top_location_proxy);
    r.flood_exposure_pct = Number(r.flood_exposure_pct);
    r.chirps_near_site_mm = Number(r.chirps_near_site_mm);
  });

  buildKpis(summary, table);
  buildIndexComponentTable(indexComponents);
  buildTopTable(table);
  buildNotes(summary);
  await buildMap(table);
  await buildSubareaMap(subRows);
}

main().catch((err) => {
  // Keep failure visible in-browser during rapid analysis workflows.
  console.error(err);
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<pre style="background:#fee2e2;padding:10px;border:1px solid #ef4444">Dashboard error: ${String(err)}</pre>`
  );
});

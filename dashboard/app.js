async function loadCsv(path) {
  const text = await fetch(path).then((r) => r.text());
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const cols = header.split(",");

  return rows.map((row) => {
    const values = row.split(",");
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

function buildNotes(summary) {
  const notes = document.getElementById("tradeoffNotes");
  const addOn = [
    "Tradeoff: IPC and MPI are primarily available at Admin-1 or selected livelihood-zone level, so county-level poverty indicators were propagated to Admin-2 unless a specific subpopulation estimate existed.",
    "Tradeoff: The final weighting (60% disaster, 40% poverty) prioritizes recent shock impact under rapid-response constraints, while still preserving poverty targeting intent.",
    "Allocation method: 5,000 slots were distributed proportionally across the top-priority Admin-2 areas using priority score x 2026 population as a proxy for likely need intensity."
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
          `Recommended beneficiaries: ${formatInt(row.recommended_beneficiaries)}`
      );
    }
  }).addTo(map);

  map.fitBounds(layer.getBounds());
  addLegend(map);
}

async function main() {
  const [table, indexComponents, summary] = await Promise.all([
    loadCsv("data/admin2_priority_table.csv"),
    loadCsv("data/index_components.csv"),
    fetch("data/summary.json").then((r) => r.json())
  ]);

  table.forEach((r) => {
    r.priority_rank = Number(r.priority_rank);
    r.priority_score = Number(r.priority_score);
    r.recommended_beneficiaries = Number(r.recommended_beneficiaries);
  });

  buildKpis(summary, table);
  buildIndexComponentTable(indexComponents);
  buildTopTable(table);
  buildNotes(summary);
  await buildMap(table);
}

main().catch((err) => {
  // Keep failure visible in-browser during rapid analysis workflows.
  console.error(err);
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<pre style="background:#fee2e2;padding:10px;border:1px solid #ef4444">Dashboard error: ${String(err)}</pre>`
  );
});

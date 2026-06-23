# Kenya Flooding 2026 Targeting Dashboard

This folder contains a GitHub Pages-ready dashboard for the emergency targeting scenario.

## What is included

- `index.html`: Main dashboard page with index design, map, table, and recommendation notes.
- `styles.css`: Styling for the dashboard.
- `app.js`: Client-side script that loads generated outputs and renders components.
- `data/admin2_priority_table.csv`: Admin-2 ranked prioritization table from real source data.
- `data/admin2_priority.geojson`: GeoJSON for map rendering.
- `data/index_components.csv`: Index design table (dimensions, indicators, weights, sources).
- `data/summary.json`: Topline stats and assumptions.

## Regenerate data outputs

From repository root:

```bash
python scripts/build_targeting_outputs.py
```

## Run locally

From repository root:

```bash
python -m http.server 8000
```

Then open:

- `http://localhost:8000/dashboard/`

## Notes

- The script maps IPC county-level values to all Admin-2 units in each county unless a specific subpopulation match is available.
- `Dadaab` is mapped directly to Dadaab Admin-2.
- `Kakuma` and `Kalobeyei` IPC values are mapped to Turkana West as a hotspot proxy.

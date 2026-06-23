from __future__ import annotations

from pathlib import Path
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterstats import zonal_stats


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "Skills Test"
OUT_DIR = BASE_DIR / "dashboard" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SUBAREA_BUFFER_METERS = 3000
SUBAREA_TOP_N = 20


def normalize_name(value: str) -> str:
    if pd.isna(value):
        return ""
    cleaned = str(value).strip().lower()
    for ch in ["'", ",", ".", "-", "(", ")"]:
        cleaned = cleaned.replace(ch, " ")
    return " ".join(cleaned.split())


def minmax(series: pd.Series) -> pd.Series:
    s = series.astype(float)
    min_val = s.min()
    max_val = s.max()
    if np.isclose(max_val, min_val):
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - min_val) / (max_val - min_val)


def compute_worldpop_by_admin2(admin2: gpd.GeoDataFrame, raster_path: Path) -> pd.DataFrame:
    with rasterio.open(raster_path) as src:
        raster_nodata = src.nodata

    stats = zonal_stats(
        admin2,
        raster_path,
        stats=["sum", "count"],
        nodata=raster_nodata,
        raster_out=True,
    )

    raw_sum = []
    cleaned_sum = []
    neg_count = []
    valid_count = []

    for item in stats:
        arr = item.get("mini_raster_array")
        if arr is None:
            raw_sum.append(0.0)
            cleaned_sum.append(0.0)
            neg_count.append(0)
            valid_count.append(0)
            continue

        data = np.asarray(arr, dtype="float64")
        mask = np.zeros(data.shape, dtype=bool)

        if np.ma.isMaskedArray(arr):
            mask |= np.asarray(arr.mask)
        if raster_nodata is not None and not np.isnan(raster_nodata):
            mask |= data == raster_nodata
        mask |= ~np.isfinite(data)

        valid = data[~mask]
        if valid.size == 0:
            raw_sum.append(0.0)
            cleaned_sum.append(0.0)
            neg_count.append(0)
            valid_count.append(0)
            continue

        raw_sum.append(float(valid.sum()))
        neg_count.append(int((valid < 0).sum()))
        valid_count.append(int(valid.size))

        # WorldPop should not have negative people; clamp negatives to zero.
        cleaned = np.where(valid < 0, 0.0, valid)
        cleaned_sum.append(float(cleaned.sum()))

    out = admin2[["admin2_name", "admin1_name", "admin2_pcode"]].copy()
    out["worldpop_raster_nodata"] = raster_nodata
    out["worldpop_raw_sum"] = raw_sum
    out["worldpop_clean_sum"] = cleaned_sum
    out["worldpop_negative_cell_count"] = neg_count
    out["worldpop_valid_cell_count"] = valid_count
    return out


def compute_flood_extent_pct(admin2: gpd.GeoDataFrame, flood: gpd.GeoDataFrame) -> pd.Series:
    admin_area = admin2.to_crs(6933)
    flood_area = flood.to_crs(6933)
    flood_sindex = flood_area.sindex

    flooded_ratio = []
    for geom in admin_area.geometry:
        if geom.is_empty:
            flooded_ratio.append(0.0)
            continue

        candidate_idx = list(flood_sindex.intersection(geom.bounds))
        if not candidate_idx:
            flooded_ratio.append(0.0)
            continue

        candidates = flood_area.iloc[candidate_idx]
        candidates = candidates[candidates.intersects(geom)]
        if candidates.empty:
            flooded_ratio.append(0.0)
            continue

        flooded_area = candidates.intersection(geom).area.sum()
        total_area = geom.area
        flooded_ratio.append(float(flooded_area / total_area) if total_area > 0 else 0.0)

    return pd.Series(flooded_ratio, index=admin2.index)


def build_subarea_targeting(
    admin2: gpd.GeoDataFrame,
    flood_extent: gpd.GeoDataFrame,
    worldpop_path: Path,
    chirps_path: Path,
) -> tuple[str, str]:
    focal = admin2.iloc[[0]].copy()
    focal_name = str(focal.iloc[0]["admin2_name"])
    focal_county = str(focal.iloc[0]["admin1_name"])

    places = gpd.read_file(DATA_DIR / "hotosm_ken_populated_places_osm_shp" / "populated_places_points.shp")
    if places.crs != admin2.crs:
        places = places.to_crs(admin2.crs)

    focal_geom = focal.geometry.iloc[0]
    places = places[places.within(focal_geom)].copy()

    if places.empty:
        fallback = gpd.GeoDataFrame(
            {
                "site_name": [f"{focal_name} centroid"],
                "site_type": ["centroid_fallback"],
                "admin2_name": [focal_name],
                "admin1_name": [focal_county],
                "population_near_site": [float(focal.iloc[0]["population_2026"])],
                "flood_exposure_pct": [float(focal.iloc[0]["flood_extent_pct"])],
                "chirps_near_site_mm": [float(focal.iloc[0]["chirps_mean_mm"])],
                "local_disaster_score": [1.0],
                "subarea_priority_score": [100.0],
                "is_top_location_proxy": [1],
            },
            geometry=[focal.geometry.centroid.iloc[0]],
            crs=admin2.crs,
        )
        fallback.to_file(OUT_DIR / "focal_subarea_priority.geojson", driver="GeoJSON")
        fallback.drop(columns=["geometry"]).to_csv(OUT_DIR / "focal_subarea_priority.csv", index=False)
        gpd.GeoDataFrame(focal, geometry="geometry", crs=admin2.crs).to_file(
            OUT_DIR / "focal_adm2.geojson", driver="GeoJSON"
        )
        gpd.GeoDataFrame(columns=["DN", "geometry"], geometry="geometry", crs=admin2.crs).to_file(
            OUT_DIR / "focal_flood_extent.geojson", driver="GeoJSON"
        )
        return focal_name, focal_county

    if "name" in places.columns:
        places["site_name"] = places["name"].astype(str).replace("nan", "").str.strip()
    else:
        places["site_name"] = ""
    places["site_name"] = places["site_name"].replace("", np.nan)
    generated_names = pd.Series(
        [f"Populated place {i}" for i in range(1, len(places) + 1)],
        index=places.index,
    )
    places["site_name"] = places["site_name"].fillna(generated_names)
    places["site_type"] = places.get("place", "unknown").fillna("unknown").astype(str)

    # Build equal-area buffers for locality-level population/disaster extraction.
    places_ll = places.to_crs(4326)
    places_m = places_ll.to_crs(6933)
    buffer_m = places_m.copy()
    buffer_m["geometry"] = places_m.buffer(SUBAREA_BUFFER_METERS)
    buffers_ll = buffer_m.to_crs(4326)

    with rasterio.open(worldpop_path) as src:
        wp_nodata = src.nodata

    pop_stats = zonal_stats(buffers_ll, worldpop_path, stats=["sum"], nodata=wp_nodata)
    pop_vals = [float(x.get("sum") or 0.0) for x in pop_stats]

    flood_exposure = compute_flood_extent_pct(buffers_ll, flood_extent)
    chirps_stats = zonal_stats(buffers_ll, chirps_path, stats=["mean"], nodata=-9999)
    chirps_vals = [float(x.get("mean") or 0.0) for x in chirps_stats]

    sub = places_ll[["site_name", "site_type", "geometry"]].copy()
    sub["admin2_name"] = focal_name
    sub["admin1_name"] = focal_county
    sub["population_near_site"] = np.clip(np.array(pop_vals, dtype=float), 0.0, None)
    sub["flood_exposure_pct"] = flood_exposure.values
    sub["chirps_near_site_mm"] = chirps_vals

    sub["flood_n"] = minmax(sub["flood_exposure_pct"])
    sub["chirps_n"] = minmax(sub["chirps_near_site_mm"])
    sub["local_disaster_score"] = 0.70 * sub["flood_n"] + 0.30 * sub["chirps_n"]
    sub["targeting_weight"] = sub["local_disaster_score"] * sub["population_near_site"]
    sub["subarea_priority_score"] = 100 * minmax(sub["targeting_weight"])

    sub = sub.sort_values("subarea_priority_score", ascending=False).reset_index(drop=True)
    sub["subarea_rank"] = np.arange(1, len(sub) + 1)
    sub["is_top_location_proxy"] = 0
    if len(sub) > 0:
        sub.loc[0, "is_top_location_proxy"] = 1

    # Save focal-area artifacts for dashboard map/table.
    sub_out = sub[
        [
            "subarea_rank",
            "site_name",
            "site_type",
            "admin2_name",
            "admin1_name",
            "population_near_site",
            "flood_exposure_pct",
            "chirps_near_site_mm",
            "local_disaster_score",
            "subarea_priority_score",
            "is_top_location_proxy",
            "geometry",
        ]
    ].copy()
    sub_out.to_file(OUT_DIR / "focal_subarea_priority.geojson", driver="GeoJSON")
    sub_out.drop(columns=["geometry"]).to_csv(OUT_DIR / "focal_subarea_priority.csv", index=False)

    gpd.GeoDataFrame(focal, geometry="geometry", crs=admin2.crs).to_file(OUT_DIR / "focal_adm2.geojson", driver="GeoJSON")
    clipped_flood = gpd.clip(flood_extent, focal.to_crs(flood_extent.crs))
    clipped_flood.to_file(OUT_DIR / "focal_flood_extent.geojson", driver="GeoJSON")

    return focal_name, focal_county


def main() -> None:
    admin2 = gpd.read_file(DATA_DIR / "ken_admin_boundaries.shp" / "ken_admin2.shp")
    admin2 = admin2[["adm2_name", "adm1_name", "adm2_pcode", "area_sqkm", "geometry"]].copy()
    admin2 = admin2.rename(
        columns={
            "adm2_name": "admin2_name",
            "adm1_name": "admin1_name",
            "adm2_pcode": "admin2_pcode",
        }
    )

    admin2["admin1_norm"] = admin2["admin1_name"].map(normalize_name)
    admin2["admin2_norm"] = admin2["admin2_name"].map(normalize_name)

    flood_extent = gpd.read_file(
        DATA_DIR
        / "maximum_flood_extent_2026-03-01_2026-04-15_kenya_2026_06_17T17_45_20_924658"
        / "maximum_flood_extent_2026-03-01_2026-04-15_kenya_2026_06_17T17_45_20_924658.geojson"
    )
    if flood_extent.crs != admin2.crs:
        flood_extent = flood_extent.to_crs(admin2.crs)

    admin2["flood_extent_pct"] = compute_flood_extent_pct(admin2, flood_extent)

    admin2["chirps_mean_mm"] = pd.Series(
        [x["mean"] for x in zonal_stats(admin2, DATA_DIR / "chirps_daily_kenya_2026_04_10.tif", stats=["mean"], nodata=-9999)],
        index=admin2.index,
    ).fillna(0.0)

    admin2["floodscan_mean"] = pd.Series(
        [x["mean"] for x in zonal_stats(admin2, DATA_DIR / "ken_floodscan_max_sfed_2025-03-01_2025-04-17.tif", stats=["mean"], nodata=-9999)],
        index=admin2.index,
    ).fillna(0.0)

    worldpop_path = DATA_DIR / "ken_pop_2026_CN_100m_R2025A_v1.tif"
    worldpop_df = compute_worldpop_by_admin2(admin2, worldpop_path)
    admin2 = admin2.merge(
        worldpop_df[["admin2_pcode", "worldpop_clean_sum", "worldpop_raw_sum", "worldpop_negative_cell_count"]],
        on="admin2_pcode",
        how="left",
    )
    admin2["population_2026"] = admin2["worldpop_clean_sum"].fillna(0.0)

    mpi = pd.read_csv(DATA_DIR / "ken_mpi.csv")
    mpi = mpi[mpi["Admin 1 Name"].notna()].copy()
    mpi["admin1_norm"] = mpi["Admin 1 Name"].map(normalize_name)
    mpi = mpi[["admin1_norm", "MPI", "Headcount Ratio", "In Severe Poverty"]].drop_duplicates(subset=["admin1_norm"])
    admin2 = admin2.merge(mpi, on="admin1_norm", how="left")

    ipc = pd.read_excel(
        DATA_DIR / "IPC_KE_Acute_Food_Insecurity_Analysis___KE_February_2026_2026-06-23.xlsx",
        sheet_name="Population Table",
    )
    ipc = ipc[ipc["Area Name"].notna()].copy()
    ipc["area_name"] = ipc["Area Name"].astype(str).str.strip()
    ipc["area_norm"] = ipc["area_name"].map(normalize_name)

    ipc_county = ipc[ipc["area_norm"].isin(set(admin2["admin1_norm"]))][
        ["area_norm", "Current - Phase 3+ %", "Current - Phase"]
    ].copy()
    ipc_county = ipc_county.rename(
        columns={
            "area_norm": "admin1_norm",
            "Current - Phase 3+ %": "ipc_phase3plus_pct",
            "Current - Phase": "ipc_phase_label",
        }
    )

    admin2 = admin2.merge(ipc_county, on="admin1_norm", how="left")

    # Subpopulation adjustments from IPC areas that are more specific than county level.
    special = ipc.set_index("area_norm")
    if "dadaab" in special.index:
        dadaab_val = float(special.loc["dadaab", "Current - Phase 3+ %"])
        admin2.loc[admin2["admin2_norm"] == "dadaab", "ipc_phase3plus_pct"] = dadaab_val
        admin2.loc[admin2["admin2_norm"] == "dadaab", "ipc_phase_label"] = "P4"

    if {"kakuma", "kalobeyei"}.issubset(set(special.index)):
        turkana_hotspot = float(
            max(
                special.loc["kakuma", "Current - Phase 3+ %"],
                special.loc["kalobeyei", "Current - Phase 3+ %"],
            )
        )
        admin2.loc[
            (admin2["admin1_norm"] == "turkana") & (admin2["admin2_norm"] == "turkana west"),
            "ipc_phase3plus_pct",
        ] = turkana_hotspot
        admin2.loc[
            (admin2["admin1_norm"] == "turkana") & (admin2["admin2_norm"] == "turkana west"),
            "ipc_phase_label",
        ] = "P4"

    admin2["MPI"] = admin2["MPI"].fillna(admin2["MPI"].median())
    admin2["Headcount Ratio"] = admin2["Headcount Ratio"].fillna(admin2["Headcount Ratio"].median())
    admin2["In Severe Poverty"] = admin2["In Severe Poverty"].fillna(admin2["In Severe Poverty"].median())
    admin2["ipc_phase3plus_pct"] = admin2["ipc_phase3plus_pct"].fillna(admin2["ipc_phase3plus_pct"].median())

    # Indicator normalization
    admin2["flood_extent_n"] = minmax(admin2["flood_extent_pct"])
    admin2["chirps_n"] = minmax(admin2["chirps_mean_mm"])
    admin2["floodscan_n"] = minmax(admin2["floodscan_mean"])
    admin2["mpi_n"] = minmax(admin2["MPI"])
    admin2["ipc_n"] = minmax(admin2["ipc_phase3plus_pct"])

    # Composite scores
    admin2["disaster_score"] = (
        0.50 * admin2["flood_extent_n"] + 0.30 * admin2["chirps_n"] + 0.20 * admin2["floodscan_n"]
    )
    admin2["poverty_score"] = 0.60 * admin2["mpi_n"] + 0.40 * admin2["ipc_n"]
    admin2["priority_score"] = 100 * (0.75 * admin2["disaster_score"] + 0.25 * admin2["poverty_score"])

    admin2["affected_population_proxy"] = admin2["population_2026"] * admin2["disaster_score"]

    admin2 = admin2.sort_values("priority_score", ascending=False).reset_index(drop=True)
    admin2["priority_rank"] = np.arange(1, len(admin2) + 1)

    # Operational strategy: concentrate all 5,000 recipients in the highest-priority Admin-2.
    admin2["recommended_beneficiaries"] = 0
    if len(admin2) > 0:
        admin2.loc[0, "recommended_beneficiaries"] = 5000

    admin2["priority_tier"] = pd.qcut(
        admin2["priority_score"],
        q=[0, 0.2, 0.4, 0.6, 0.8, 1.0],
        labels=["Very Low", "Low", "Medium", "High", "Very High"],
    )

    result_cols = [
        "priority_rank",
        "admin2_name",
        "admin1_name",
        "admin2_pcode",
        "priority_tier",
        "priority_score",
        "disaster_score",
        "poverty_score",
        "flood_extent_pct",
        "chirps_mean_mm",
        "floodscan_mean",
        "MPI",
        "Headcount Ratio",
        "In Severe Poverty",
        "ipc_phase3plus_pct",
        "ipc_phase_label",
        "population_2026",
        "affected_population_proxy",
        "recommended_beneficiaries",
    ]

    admin2[result_cols].to_csv(OUT_DIR / "admin2_priority_table.csv", index=False)
    worldpop_df.to_csv(OUT_DIR / "worldpop_by_adm2.csv", index=False)

    map_gdf = admin2[result_cols + ["geometry"]].copy()
    map_gdf.to_file(OUT_DIR / "admin2_priority.geojson", driver="GeoJSON")

    index_components = pd.DataFrame(
        [
            {"dimension": "Disaster", "indicator": "Flood extent share", "weight_within_dimension": 0.50, "source": "GloFAS maximum flood extent"},
            {"dimension": "Disaster", "indicator": "CHIRPS daily precipitation (2026-04-10)", "weight_within_dimension": 0.30, "source": "CHIRPS"},
            {"dimension": "Disaster", "indicator": "FloodScan max SFED mean", "weight_within_dimension": 0.20, "source": "FloodScan"},
            {"dimension": "Poverty", "indicator": "Multidimensional Poverty Index (MPI)", "weight_within_dimension": 0.60, "source": "HDX Kenya MPI"},
            {"dimension": "Poverty", "indicator": "IPC Current Phase 3+ percent", "weight_within_dimension": 0.40, "source": "IPC acute food insecurity"},
            {"dimension": "Final Composite", "indicator": "Disaster dimension", "weight_within_dimension": 0.75, "source": "Custom weighting"},
            {"dimension": "Final Composite", "indicator": "Poverty dimension", "weight_within_dimension": 0.25, "source": "Custom weighting"},
        ]
    )
    index_components.to_csv(OUT_DIR / "index_components.csv", index=False)

    focal_adm2_name, focal_county_name = build_subarea_targeting(
        admin2,
        flood_extent,
        worldpop_path,
        DATA_DIR / "chirps_daily_kenya_2026_04_10.tif",
    )

    top10 = admin2.head(10)[["priority_rank", "admin2_name", "admin1_name", "priority_score", "recommended_beneficiaries"]]
    summary = {
        "total_admin2": int(len(admin2)),
        "total_recommended_beneficiaries": int(admin2["recommended_beneficiaries"].sum()),
        "top10": top10.to_dict(orient="records"),
        "assumptions": [
            "Index design: Final index weighting uses 75% disaster and 25% poverty to emphasize immediate flood impact under rapid-response constraints.",
            f"Allocation strategy: All 5,000 beneficiaries are concentrated in the single highest-priority Admin-2, {focal_adm2_name} ({focal_county_name}).",
            "Subarea targeting: Within the top Admin-2, locality proxies are derived from populated places and ranked by local disaster score to support decision-maker discretion.",
            "Decision support: No automatic proportional recipient split is applied at the subarea level; decision makers select among top-ranked locality proxies.",
        ],
    }
    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("Wrote:")
    for p in [
        OUT_DIR / "admin2_priority_table.csv",
        OUT_DIR / "admin2_priority.geojson",
        OUT_DIR / "index_components.csv",
        OUT_DIR / "worldpop_by_adm2.csv",
        OUT_DIR / "focal_subarea_priority.csv",
        OUT_DIR / "focal_subarea_priority.geojson",
        OUT_DIR / "focal_adm2.geojson",
        OUT_DIR / "focal_flood_extent.geojson",
        OUT_DIR / "summary.json",
    ]:
        print(" -", p)


if __name__ == "__main__":
    main()

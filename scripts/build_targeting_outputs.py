from __future__ import annotations

from pathlib import Path
import json

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterstats import zonal_stats


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "Skills Test"
OUT_DIR = BASE_DIR / "dashboard" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)


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


def allocate_beneficiaries(df: pd.DataFrame, total_beneficiaries: int = 5000, top_n: int = 40) -> pd.Series:
    allocation = pd.Series(np.zeros(len(df), dtype=int), index=df.index)
    top_idx = df.head(top_n).index
    weights = (df.loc[top_idx, "priority_score"] / 100.0) * df.loc[top_idx, "population_2026"]

    if np.isclose(weights.sum(), 0.0):
        return allocation

    raw = (weights / weights.sum()) * total_beneficiaries
    floor_vals = np.floor(raw).astype(int)
    remainder = total_beneficiaries - int(floor_vals.sum())

    allocation.loc[top_idx] = floor_vals
    if remainder > 0:
        frac = (raw - floor_vals).sort_values(ascending=False)
        for idx in frac.index[:remainder]:
            allocation.loc[idx] += 1

    return allocation


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

    admin2["population_2026"] = pd.Series(
        [x["sum"] for x in zonal_stats(admin2, DATA_DIR / "ken_pop_2026_CN_100m_R2025A_v1.tif", stats=["sum"], nodata=-9999)],
        index=admin2.index,
    ).fillna(0.0)

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
    admin2["priority_score"] = 100 * (0.60 * admin2["disaster_score"] + 0.40 * admin2["poverty_score"])

    admin2["affected_population_proxy"] = admin2["population_2026"] * admin2["disaster_score"]

    admin2 = admin2.sort_values("priority_score", ascending=False).reset_index(drop=True)
    admin2["priority_rank"] = np.arange(1, len(admin2) + 1)

    admin2["recommended_beneficiaries"] = allocate_beneficiaries(admin2, total_beneficiaries=5000, top_n=40)

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

    map_gdf = admin2[result_cols + ["geometry"]].copy()
    map_gdf.to_file(OUT_DIR / "admin2_priority.geojson", driver="GeoJSON")

    index_components = pd.DataFrame(
        [
            {"dimension": "Disaster", "indicator": "Flood extent share", "weight_within_dimension": 0.50, "source": "GloFAS maximum flood extent"},
            {"dimension": "Disaster", "indicator": "CHIRPS daily precipitation (2026-04-10)", "weight_within_dimension": 0.30, "source": "CHIRPS"},
            {"dimension": "Disaster", "indicator": "FloodScan max SFED mean", "weight_within_dimension": 0.20, "source": "FloodScan"},
            {"dimension": "Poverty", "indicator": "Multidimensional Poverty Index (MPI)", "weight_within_dimension": 0.60, "source": "HDX Kenya MPI"},
            {"dimension": "Poverty", "indicator": "IPC Current Phase 3+ percent", "weight_within_dimension": 0.40, "source": "IPC acute food insecurity"},
            {"dimension": "Final Composite", "indicator": "Disaster dimension", "weight_within_dimension": 0.60, "source": "Custom weighting"},
            {"dimension": "Final Composite", "indicator": "Poverty dimension", "weight_within_dimension": 0.40, "source": "Custom weighting"},
        ]
    )
    index_components.to_csv(OUT_DIR / "index_components.csv", index=False)

    top10 = admin2.head(10)[["priority_rank", "admin2_name", "admin1_name", "priority_score", "recommended_beneficiaries"]]
    summary = {
        "total_admin2": int(len(admin2)),
        "total_recommended_beneficiaries": int(admin2["recommended_beneficiaries"].sum()),
        "top10": top10.to_dict(orient="records"),
        "assumptions": [
            "Admin-1 poverty/IPC indicators were propagated to Admin-2 where sub-county values were unavailable.",
            "IPC special areas were mapped as follows: Dadaab -> Dadaab Admin-2; Kakuma/Kalobeyei -> Turkana West Admin-2.",
            "Beneficiary allocation of 5,000 is proportional to priority_score * population for top 40 Admin-2 units.",
        ],
    }
    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("Wrote:")
    for p in [
        OUT_DIR / "admin2_priority_table.csv",
        OUT_DIR / "admin2_priority.geojson",
        OUT_DIR / "index_components.csv",
        OUT_DIR / "summary.json",
    ]:
        print(" -", p)


if __name__ == "__main__":
    main()

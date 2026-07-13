"""
Smart Material Estimator — Allocation Engine  (Phase 2 + 3)
=============================================================
Exports
-------
  build_demand_matrix(equip, recipe, inv)   → demand_df, inv_clean
  allocate_sequential(demand_df, inv_clean, priority_order)
      → allocation_df
  compute_feasibility(allocation_df)
      → feasibility_df
  run_suggestion_engine(demand_df, inv_clean, priority_order)
      → suggestion_df, detail_df
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2-A  Build the demand matrix
# ─────────────────────────────────────────────────────────────────────────────

def build_demand_matrix(
    equip: pd.DataFrame,
    recipe: pd.DataFrame,
    inv: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Join Equipment × Recipe on Lining_System_Code, then compute per-row demand.

    Returns
    -------
    demand_df : one row per (Equipment_Tag_No., Material_Code) with columns:
                  Equipment_Tag_No. | Name | Material_Code | Material_Name |
                  UOM | Demand_Qty
    inv_clean : inventory aggregated and filled (Available_Qty = 0 for
                materials present in recipe but absent from inventory)
    """

    # ── Join equipment rows to their lining-system recipes ──────────────
    merged = pd.merge(
        equip,
        recipe,
        on="Lining_System_Code",
        how="left",
        suffixes=("_equip", "_recipe"),
    )

    merged["Demand_Qty"] = merged["For_1_SQM"] * merged["Surface_Area_SQM"]

    # ── Aggregate: a tag with multiple lining systems sums demand per material
    demand_df = (
        merged.groupby(
            ["Equipment_Tag_No.", "Name", "Material_Code", "Material_Name",
             "UOM"],
            as_index=False,
        )["Demand_Qty"]
        .sum()
    )

    # ── Fill inventory for materials not in File A (treat as 0 stock) ───
    all_mat_in_demand = demand_df[["Material_Code"]].drop_duplicates()
    inv_full = pd.merge(
        all_mat_in_demand,
        inv[["Material_Code", "Material_Name", "Nature", "UOM", "Available_Qty"]],
        on="Material_Code",
        how="left",
    )
    inv_full["Available_Qty"] = inv_full["Available_Qty"].fillna(0.0)
    inv_full["Material_Name"] = inv_full["Material_Name"].fillna(
        demand_df.set_index("Material_Code")["Material_Name"].to_dict()
    )

    return demand_df, inv_full


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2-B  Sequential priority allocation
# ─────────────────────────────────────────────────────────────────────────────

def allocate_sequential(
    demand_df: pd.DataFrame,
    inv_clean: pd.DataFrame,
    priority_order: List[str],
) -> pd.DataFrame:
    """
    Allocate inventory to equipment in strict priority order.

    For each material independently:
      - Available budget  = Available_Qty (from inventory)
      - Work through equipment in priority_order (top = highest priority)
      - Give each equipment min(its demand, remaining budget)
      - Record Allocated_Qty and Shortfall_Qty per row

    Parameters
    ----------
    demand_df      : output of build_demand_matrix()
    inv_clean      : output of build_demand_matrix()
    priority_order : list of Equipment_Tag_No. strings in desired build order

    Returns
    -------
    allocation_df : demand_df enriched with:
                    Priority_Rank | Allocated_Qty | Shortfall_Qty |
                    Fulfillment_Rate (0.0 – 1.0)
    """

    # Build rank lookup  {tag: rank_integer starting at 1}
    rank_map = {tag: i + 1 for i, tag in enumerate(priority_order)}

    # Add any tags not in priority list at the end (safety net)
    unranked_tags = [
        t for t in demand_df["Equipment_Tag_No."].unique()
        if t not in rank_map
    ]
    max_rank = len(rank_map)
    for i, tag in enumerate(unranked_tags):
        rank_map[tag] = max_rank + i + 1

    # Attach rank to demand rows
    df = demand_df.copy()
    df["Priority_Rank"] = df["Equipment_Tag_No."].map(rank_map)
    df = df.sort_values(["Material_Code", "Priority_Rank"]).reset_index(drop=True)

    # Available budget per material (mutable dict for the loop)
    budget: dict[str, float] = (
        inv_clean.set_index("Material_Code")["Available_Qty"].to_dict()
    )
    # Default 0 for any material not in inventory dict
    all_materials = df["Material_Code"].unique()
    for m in all_materials:
        if m not in budget:
            budget[m] = 0.0

    # ── Allocation loop ──────────────────────────────────────────────
    allocated_list: list[float] = []
    shortfall_list: list[float] = []

    for _, row in df.iterrows():
        mat      = row["Material_Code"]
        demand   = row["Demand_Qty"]
        avail    = budget[mat]

        given    = min(demand, avail)
        short    = demand - given

        budget[mat] -= given

        allocated_list.append(round(given,   6))
        shortfall_list.append(round(short,   6))

    df["Allocated_Qty"] = allocated_list
    df["Shortfall_Qty"] = shortfall_list
    df["Fulfillment_Rate"] = np.where(
        df["Demand_Qty"] > 0,
        df["Allocated_Qty"] / df["Demand_Qty"],
        1.0,
    ).clip(0.0, 1.0).round(6)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2-C  Feasibility summary per equipment
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_FULL    = "✅ 100% Fully Ready to Build"
_STATUS_PARTIAL = "🟡 Partially Ready"
_STATUS_BLOCKED = "🔴 Blocked by Shortages"


def compute_feasibility(allocation_df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse allocation_df to one row per equipment with:
      Priority_Rank | Equipment_Tag_No. | Name |
      Total_Demand_Qty | Total_Allocated_Qty | Total_Shortfall_Qty |
      Completion_Pct   (demand-weighted average fulfillment) |
      Bottleneck_Material (material with worst fulfillment rate) |
      Status

    Logic
    -----
    - All materials 100% fulfilled  → Fully Ready
    - Zero allocation on ANY material → Blocked
    - Otherwise                     → Partially Ready
    """
    grp = allocation_df.groupby(
        ["Equipment_Tag_No.", "Name", "Priority_Rank"], as_index=False
    ).agg(
        Total_Demand_Qty    =("Demand_Qty",       "sum"),
        Total_Allocated_Qty =("Allocated_Qty",    "sum"),
        Total_Shortfall_Qty =("Shortfall_Qty",    "sum"),
        Min_Fulfillment     =("Fulfillment_Rate",  "min"),   # worst material
    )

    grp["Completion_Pct"] = (
        grp["Total_Allocated_Qty"] / grp["Total_Demand_Qty"] * 100
    ).clip(0, 100).round(2)

    grp["Status"] = grp.apply(_status_label, axis=1)

    # Find the single worst bottleneck material per equipment
    worst = (
        allocation_df.sort_values("Fulfillment_Rate")
        .groupby("Equipment_Tag_No.", as_index=False)
        .first()[["Equipment_Tag_No.", "Material_Code", "Material_Name",
                   "Shortfall_Qty"]]
        .rename(columns={
            "Material_Code":   "Bottleneck_Material_Code",
            "Material_Name":   "Bottleneck_Material_Name",
            "Shortfall_Qty":   "Bottleneck_Shortfall",
        })
    )
    # Only show bottleneck if there actually is a shortfall
    worst.loc[worst["Bottleneck_Shortfall"] <= 0,
              ["Bottleneck_Material_Code", "Bottleneck_Material_Name",
               "Bottleneck_Shortfall"]] = ["—", "—", 0.0]

    feasibility = pd.merge(grp, worst, on="Equipment_Tag_No.", how="left")
    feasibility = feasibility.sort_values("Priority_Rank").reset_index(drop=True)

    return feasibility[[
        "Priority_Rank", "Equipment_Tag_No.", "Name",
        "Total_Demand_Qty", "Total_Allocated_Qty", "Total_Shortfall_Qty",
        "Completion_Pct", "Status",
        "Bottleneck_Material_Code", "Bottleneck_Material_Name",
        "Bottleneck_Shortfall",
    ]]


def _status_label(row: pd.Series) -> str:
    if row["Total_Shortfall_Qty"] <= 0:
        return _STATUS_FULL
    if row["Min_Fulfillment"] == 0.0:
        return _STATUS_BLOCKED
    return f"{_STATUS_PARTIAL} ({row['Completion_Pct']:.1f}%)"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3   Predictive Suggestion Engine
# ─────────────────────────────────────────────────────────────────────────────

def run_suggestion_engine(
    demand_df: pd.DataFrame,
    inv_clean: pd.DataFrame,
    priority_order: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    For every equipment that is NOT Fully Ready (candidate to pause),
    simulate pausing it and re-run allocation on the rest.

    Primary metric  — Newly_Completable_Count : how many go from non-full → full.
    Secondary metric — Avg_Completion_Gain    : average % point improvement
                        across all remaining equipment (used when nothing
                        becomes fully completable to still rank scenarios).

    Returns
    -------
    suggestion_df : summary table — one row per candidate-to-pause.
    detail_df     : detailed feasibility table for the single best scenario.
    """

    # ── Baseline ─────────────────────────────────────────────────────
    baseline_alloc = allocate_sequential(demand_df, inv_clean, priority_order)
    baseline_feas  = compute_feasibility(baseline_alloc)
    baseline_full  = set(
        baseline_feas.loc[
            baseline_feas["Status"] == _STATUS_FULL, "Equipment_Tag_No."
        ]
    )
    baseline_completion = baseline_feas.set_index("Equipment_Tag_No.")["Completion_Pct"]

    not_full_tags = baseline_feas.loc[
        baseline_feas["Status"] != _STATUS_FULL, "Equipment_Tag_No."
    ].tolist()

    rows: list[dict] = []
    best_score       = (-1, -999.0)   # (newly_full_count, avg_gain)
    best_detail_df   = pd.DataFrame()

    for pause_tag in not_full_tags:
        reduced_demand = demand_df[demand_df["Equipment_Tag_No."] != pause_tag].copy()
        reduced_order  = [t for t in priority_order if t != pause_tag]

        sim_alloc  = allocate_sequential(reduced_demand, inv_clean, reduced_order)
        sim_feas   = compute_feasibility(sim_alloc)
        sim_full   = set(
            sim_feas.loc[
                sim_feas["Status"] == _STATUS_FULL, "Equipment_Tag_No."
            ]
        )
        sim_completion = sim_feas.set_index("Equipment_Tag_No.")["Completion_Pct"]

        # Newly fully completable (excludes the paused tag itself)
        newly_full      = sim_full - baseline_full
        newly_full_list = sorted(newly_full)
        net_gain        = len(newly_full) - 1   # −1 for the one paused

        # Average completion % gain across all remaining equipment
        common_tags = [
            t for t in baseline_completion.index
            if t != pause_tag and t in sim_completion.index
        ]
        gains = [
            sim_completion[t] - baseline_completion[t] for t in common_tags
        ]
        avg_gain = float(np.mean(gains)) if gains else 0.0

        pause_name = demand_df.loc[
            demand_df["Equipment_Tag_No."] == pause_tag, "Name"
        ].iloc[0] if not demand_df[demand_df["Equipment_Tag_No."] == pause_tag].empty \
            else pause_tag

        rows.append({
            "Pause_Tag":               pause_tag,
            "Pause_Name":              pause_name,
            "Newly_Completable_Count": len(newly_full),
            "Newly_Completable_Tags":  ", ".join(newly_full_list) if newly_full_list else "—",
            "Avg_Completion_Gain_Pct": round(avg_gain, 2),
            "Net_Gain_Score":          net_gain,
            "Recommended":             False,
        })

        score = (len(newly_full), avg_gain)
        if score > best_score:
            best_score     = score
            best_detail_df = sim_feas.copy()
            best_detail_df["Scenario"] = f"If '{pause_tag}' is paused"

    suggestion_df = pd.DataFrame(rows).sort_values(
        ["Newly_Completable_Count", "Avg_Completion_Gain_Pct"],
        ascending=False,
    ).reset_index(drop=True)

    if not suggestion_df.empty:
        best_idx = suggestion_df.index[0]
        suggestion_df.loc[best_idx, "Recommended"] = True

    return suggestion_df, best_detail_df


# ─────────────────────────────────────────────────────────────────────────────
# PROCUREMENT SHOPPING LIST  (used by Tab 3)
# ─────────────────────────────────────────────────────────────────────────────

def build_procurement_list(
    allocation_df: pd.DataFrame,
    inv_clean: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate all shortfall quantities per material.
    Returns only materials with Shortage_Qty_To_Buy > 0.
    """
    shortage = (
        allocation_df.groupby("Material_Code", as_index=False)["Shortfall_Qty"]
        .sum()
        .rename(columns={"Shortfall_Qty": "Shortage_Qty_To_Buy"})
    )
    shortage = shortage[shortage["Shortage_Qty_To_Buy"] > 0]

    enriched = pd.merge(
        shortage,
        inv_clean[["Material_Code", "Material_Name", "UOM", "Available_Qty"]],
        on="Material_Code",
        how="left",
    )
    enriched["Available_Qty"]      = enriched["Available_Qty"].fillna(0)
    enriched["Shortage_Qty_To_Buy"] = enriched["Shortage_Qty_To_Buy"].round(3)

    return enriched[
        ["Material_Code", "Material_Name", "UOM",
         "Available_Qty", "Shortage_Qty_To_Buy"]
    ].sort_values("Shortage_Qty_To_Buy", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TEST  (run directly:  python allocation_engine.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    # ── Re-use the cleaned loaders from validate_data.py ─────────────
    from validate_data import load_raw, clean_inventory, clean_recipe, clean_equipment

    print("\nLoading & cleaning data …")
    df_a_raw, df_b_raw, df_c_raw = load_raw()
    inv    = clean_inventory(df_a_raw)
    recipe = clean_recipe(df_b_raw)
    equip  = clean_equipment(df_c_raw)

    # ── Build demand matrix ───────────────────────────────────────────
    print("Building demand matrix …")
    demand_df, inv_clean = build_demand_matrix(equip, recipe, inv)
    print(f"  Demand matrix : {demand_df.shape[0]} rows "
          f"({demand_df['Equipment_Tag_No.'].nunique()} equipment × "
          f"{demand_df['Material_Code'].nunique()} materials)")

    # ── Default priority = file order ─────────────────────────────────
    default_order = equip["Equipment_Tag_No."].unique().tolist()

    # ── Sequential allocation ─────────────────────────────────────────
    print("\nRunning sequential allocation (default priority) …")
    alloc_df = allocate_sequential(demand_df, inv_clean, default_order)

    # ── Feasibility report ────────────────────────────────────────────
    feas_df = compute_feasibility(alloc_df)
    print("\n" + "=" * 75)
    print("  JOB FEASIBILITY REPORT  (default priority order)")
    print("=" * 75)
    pd.set_option("display.max_colwidth", 40)
    pd.set_option("display.width", 200)
    print(feas_df[[
        "Priority_Rank", "Equipment_Tag_No.", "Name",
        "Completion_Pct", "Status", "Bottleneck_Material_Code"
    ]].to_string(index=False))

    # ── Status counts ─────────────────────────────────────────────────
    print("\n  Status summary:")
    for status, cnt in feas_df["Status"].value_counts().items():
        print(f"    {status} : {cnt}")

    # ── Procurement list ──────────────────────────────────────────────
    proc_df = build_procurement_list(alloc_df, inv_clean)
    print("\n" + "=" * 75)
    print("  PROCUREMENT SHOPPING LIST")
    print("=" * 75)
    print(proc_df.to_string(index=False))

    # ── Suggestion engine ─────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("  PREDICTIVE SUGGESTION ENGINE")
    print("=" * 75)
    sugg_df, best_scenario_df = run_suggestion_engine(
        demand_df, inv_clean, default_order
    )
    print(sugg_df[
        ["Pause_Tag", "Pause_Name", "Newly_Completable_Count",
         "Newly_Completable_Tags", "Avg_Completion_Gain_Pct", "Recommended"]
    ].to_string(index=False))

    if not best_scenario_df.empty:
        rec_row = sugg_df[sugg_df["Recommended"]].iloc[0]
        print(f"\n  ⭐ BEST RECOMMENDATION: Pause  '{rec_row['Pause_Tag']}'")
        if rec_row["Newly_Completable_Tags"] != "—":
            print(f"     This unlocks {rec_row['Newly_Completable_Count']} "
                  f"additional fully-completable equipment:")
            print(f"     → {rec_row['Newly_Completable_Tags']}")
        else:
            print(f"     No equipment reaches 100% in current inventory scenario.")
            print(f"     However, pausing this tag yields the highest average "
                  f"completion gain of +{rec_row['Avg_Completion_Gain_Pct']:.1f}% "
                  f"across all remaining equipment.")
        print("\n  Resulting feasibility in best scenario:")
        print(best_scenario_df[[
            "Priority_Rank", "Equipment_Tag_No.", "Name",
            "Completion_Pct", "Status"
        ]].to_string(index=False))

    print("\n  ✅ Allocation engine self-test complete.\n")
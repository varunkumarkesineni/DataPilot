"""Data Analysis Tool: runs the planned Pandas operation and returns a computed result."""
from __future__ import annotations

import numpy as np
import pandas as pd

from agents.question_analyzer import TIME_GROUPS
from utils import data_analysis as da
from utils.data_analysis import AnalysisError, format_number as fmt
from utils.data_loader import profile_columns


def run(plan: dict, df: pd.DataFrame) -> dict:
    """Return {intent, metrics, tables, summary, highlights}. All numbers come from Pandas."""
    handler = _HANDLERS.get(plan["tool"])
    if handler is None:
        raise AnalysisError("No analysis tool is available for this request.")
    try:
        data = da.apply_filters(df, plan["params"].get("filters"))
        if data.empty:
            raise AnalysisError("No rows match the requested filter.")
        result = handler(data, plan)
    except AnalysisError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"The calculation failed ({type(exc).__name__}).") from exc
    result["intent"] = plan["intent"]
    result["filter_note"] = _filter_note(plan)
    if result["filter_note"]:
        result["summary"] += f" ({result['filter_note']})"
    return result


def _filter_note(plan: dict) -> str:
    flt = plan["params"].get("filters") or []
    return "filtered to " + ", ".join(f"{f['column']} = {f['value']}" for f in flt) if flt else ""


def _scalar(df, plan):
    p = plan["params"]
    value = da.scalar_metric(df, p["metric"], p["agg"])
    label = f"{da.AGG_LABEL[p['agg']]} {p['metric']}"
    return {
        "metrics": {label: value, "Rows analysed": len(df)},
        "tables": [],
        "summary": f"The {label.lower()} is {fmt(value)}.",
        "highlights": [],
    }


def _group(df, plan):
    p = plan["params"]
    metric, agg, group = p["metric"], p["agg"], p["group_by"]
    if group in TIME_GROUPS:
        label, grain = TIME_GROUPS[group]
        df = df.dropna(subset=[p["date_column"]])
        df = df.assign(**{label: da.period_labels(df, p["date_column"], grain)})
        group = label
    table = da.group_aggregate(df, group, metric, agg, p.get("ascending", False))
    if table.empty:
        raise AnalysisError("No groups could be formed from the data.")
    value_col = table.columns[1]
    total = table[value_col].sum()
    best, asc = table.iloc[0], p.get("ascending", False)
    shown = table.head(p["top_n"]) if p.get("top_n") else table
    word = "lowest" if asc else "highest"

    metrics = {f"{word.title()} {group}": str(best[group]), value_col: float(best[value_col])}
    highlights = []
    if agg == "sum" and total > 0:
        pct = best[value_col] / total * 100
        metrics["Share of total"] = f"{pct:.1f}%"
        highlights.append(f"{best[group]} accounts for {pct:.1f}% of total {metric}.")
    metrics["Groups compared"] = len(table)
    if len(table) > 1:
        second, last = table.iloc[1], table.iloc[-1]
        if second[value_col]:
            gap = (best[value_col] - second[value_col]) / abs(second[value_col]) * 100
            highlights.append(
                f"{best[group]} is {abs(gap):.1f}% {'below' if gap < 0 else 'above'} the runner-up, {second[group]}."
            )
        highlights.append(f"{last[group]} sits at the other end with {fmt(last[value_col])}.")

    if plan["intent"] == "ranking":
        items = "; ".join(f"{r[group]} ({fmt(r[value_col])})" for _, r in shown.head(5).iterrows())
        summary = f"{'Bottom' if asc else 'Top'} {len(shown)} by {value_col.lower()} (grouped by {group}): {items}."
    elif plan["intent"] == "top_group":
        summary = f"{best[group]} has the {word} {value_col.lower()}: {fmt(best[value_col])}."
    else:
        last = table.iloc[-1]
        summary = (f"{value_col} by {group}: {best[group]} leads with {fmt(best[value_col])}, "
                   f"{last[group]} is lowest with {fmt(last[value_col])}.")
    return {"metrics": metrics, "tables": [(f"{value_col} by {group}", shown.head(50))],
            "summary": summary, "highlights": highlights}


def _share(df, plan):
    p = plan["params"]
    metric, group = p["metric"], p["group_by"]
    if group in TIME_GROUPS:
        label, grain = TIME_GROUPS[group]
        df = df.dropna(subset=[p["date_column"]])
        df = df.assign(**{label: da.period_labels(df, p["date_column"], grain)})
        group = label
    table = da.share_by_group(df, group, metric)
    top, bottom = table.iloc[0], table.iloc[-1]
    highlights = []
    if len(table) >= 3:
        two = table["Share %"].head(2).sum()
        highlights.append(f"The top two {group} values together account for {two:.1f}% of {metric}.")
    return {
        "metrics": {"Largest share": f"{top[group]} ({top['Share %']:.1f}%)",
                    "Smallest share": f"{bottom[group]} ({bottom['Share %']:.1f}%)",
                    "Groups": len(table)},
        "tables": [(f"Share of {metric} by {group}", table.head(50))],
        "summary": (f"{top[group]} contributes the largest share of {metric} ({top['Share %']:.1f}%), "
                    f"while {bottom[group]} contributes the smallest ({bottom['Share %']:.1f}%)."),
        "highlights": highlights,
    }


def _trend(df, plan, metric_override=None):
    p = plan["params"]
    metric = metric_override or p["metric"]
    table = da.trend_over_time(df, p["date_column"], metric, p.get("grain", "month"))
    n = len(table)
    if n < 2:
        raise AnalysisError("There are not enough time periods to describe a trend.")
    value_col = table.columns[1]
    values = table[value_col].to_numpy(dtype=float)
    slope = np.polyfit(np.arange(n), values, 1)[0]
    rel = slope / values.mean() if values.mean() else 0
    direction = "an upward" if rel > 0.01 else "a downward" if rel < -0.01 else "a roughly flat"
    first, last = table.iloc[0], table.iloc[-1]
    overall = (last[value_col] - first[value_col]) / abs(first[value_col]) * 100 if first[value_col] else float("nan")
    peak, low = table.loc[table[value_col].idxmax()], table.loc[table[value_col].idxmin()]
    metrics = {"First period": first["Period"], "Last period": last["Period"],
               "Peak period": f"{peak['Period']} ({fmt(peak[value_col])})",
               "Lowest period": f"{low['Period']} ({fmt(low[value_col])})"}
    if np.isfinite(overall):
        metrics["Change first → last"] = f"{overall:+.1f}%"
    highlights = []
    ch = table.dropna(subset=["Change %"])
    if not ch.empty:
        up, down = ch.loc[ch["Change %"].idxmax()], ch.loc[ch["Change %"].idxmin()]
        highlights.append(f"Biggest jump: {up['Period']} ({up['Change %']:+.1f}% vs previous period).")
        highlights.append(f"Biggest drop: {down['Period']} ({down['Change %']:+.1f}% vs previous period).")
    return {
        "metrics": metrics,
        "tables": [(f"{metric} over time", table)],
        "summary": (f"{metric} shows {direction} trend across {n} periods, from {first['Period']} to "
                    f"{last['Period']}, peaking in {peak['Period']}."),
        "highlights": highlights,
    }


def _outliers(df, plan, metric_override=None):
    metric = metric_override or plan["params"]["metric"]
    rows, stats = da.find_outliers(df, metric)
    metrics = {"Unusual values found": stats["count"], "Share of rows": f"{stats['pct']:.2f}%",
               "Normal range (IQR rule)": f"{stats['lower']:,.2f} to {stats['upper']:,.2f}"}
    highlights = []
    missing = int(df[metric].isna().sum())
    if missing:
        highlights.append(f"{missing} rows have no value for {metric} and were ignored.")
    if stats["count"] == 0:
        summary = f"No unusual values were found in {metric} using the IQR rule."
        tables = []
    else:
        top = rows[metric].iloc[0]
        summary = (f"{stats['count']} rows have unusual {metric} values outside "
                   f"{stats['lower']:,.2f}–{stats['upper']:,.2f}; the most extreme is {fmt(top)}.")
        highlights.append(f"The typical (median) {metric} is {fmt(stats['median'])}.")
        tables = [(f"Most unusual rows by {metric}", rows.head(20))]
    return {"metrics": metrics, "tables": tables, "summary": summary, "highlights": highlights}


def _describe(df, plan):
    numeric = profile_columns(df)["numeric"]
    if not numeric:
        raise AnalysisError("The dataset has no numeric columns to describe.")
    table = da.describe_numeric(df, numeric)
    highlights = []
    metric = plan["params"].get("metric")
    row = table[table["Column"] == metric]
    if not row.empty:
        mean, median = float(row["mean"].iloc[0]), float(row["50%"].iloc[0])
        highlights.append(f"{metric}: mean {fmt(mean)}, median {fmt(median)}.")
        if median and mean > median * 1.2:
            highlights.append(f"The mean of {metric} is well above its median, so a few large values pull the average up.")
    return {
        "metrics": {"Rows": len(df), "Numeric columns": len(numeric),
                    "Missing values": int(df.isna().sum().sum())},
        "tables": [("Descriptive statistics", table)],
        "summary": f"Descriptive statistics for {len(numeric)} numeric columns across {len(df)} rows.",
        "highlights": highlights,
    }


def _insights(df, plan):
    p = plan["params"]
    metric = p["metric"]
    prof = profile_columns(df)
    total, mean = da.scalar_metric(df, metric, "sum"), da.scalar_metric(df, metric, "mean")
    metrics = {f"Total {metric}": total, f"Average {metric}": mean, "Rows": len(df)}
    tables, highlights = [], []

    cats = [c for c in prof["categorical"] if 2 <= df[c].nunique() <= 30][:2]
    for col in cats:
        try:
            t = da.share_by_group(df, col, metric)
        except AnalysisError:
            continue
        tables.append((f"Share of {metric} by {col}", t))
        highlights.append(f"{t.iloc[0][col]} is the top {col} with {t.iloc[0]['Share %']:.1f}% of total {metric}.")
    if p.get("date_column"):
        try:
            tr = _trend(df, plan)
            tables.append(tr["tables"][0])
            highlights.append(tr["summary"])
            highlights.extend(tr["highlights"][:1])
        except AnalysisError:
            pass
    try:
        out = _outliers(df, plan)
        highlights.append(out["summary"])
    except AnalysisError:
        pass
    return {"metrics": metrics, "tables": tables,
            "summary": f"Overview of {metric}: total {fmt(total)}, average {fmt(mean)} across {len(df)} rows.",
            "highlights": highlights}


_HANDLERS = {
    "scalar": _scalar, "group_aggregate": _group, "share": _share, "trend": _trend,
    "outliers": _outliers, "describe": _describe, "insights": _insights,
}

"""Pure Pandas computations. Every number shown to the user comes from here."""
from __future__ import annotations

import numpy as np
import pandas as pd


class AnalysisError(Exception):
    """Raised when a calculation cannot be completed safely."""


AGG_LABEL = {"sum": "Total", "mean": "Average", "median": "Median", "min": "Minimum", "max": "Maximum"}


def format_number(value) -> str:
    if isinstance(value, (bool, np.bool_)):
        return str(value)
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            return "n/a"
        if float(value).is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return str(value)


def apply_filters(df: pd.DataFrame, filters: list[dict] | None) -> pd.DataFrame:
    for f in filters or []:
        col, val = f.get("column"), f.get("value")
        if col not in df.columns:
            raise AnalysisError(f"Filter column '{col}' was not found in the dataset.")
        mask = df[col].astype(str).str.strip().str.lower() == str(val).strip().lower()
        df = df[mask]
    return df


def period_labels(df: pd.DataFrame, date_col: str, grain: str = "month") -> pd.Series:
    dates = df[date_col]
    if grain == "year":
        return dates.dt.year.astype(int).astype(str)
    return dates.dt.to_period("M").astype(str)


def scalar_metric(df: pd.DataFrame, metric: str, agg: str = "sum") -> float:
    values = df[metric].dropna()
    if values.empty:
        raise AnalysisError(f"Column '{metric}' has no valid numeric values.")
    return float(getattr(values, agg)())


def group_aggregate(
    df: pd.DataFrame, group: str, metric: str, agg: str = "sum", ascending: bool = False
) -> pd.DataFrame:
    value_col = f"{AGG_LABEL.get(agg, agg.title())} {metric}"
    out = (
        df.dropna(subset=[group, metric])
        .groupby(group)[metric]
        .agg(agg)
        .reset_index(name=value_col)
        .sort_values(value_col, ascending=ascending, kind="stable")
        .reset_index(drop=True)
    )
    out[value_col] = out[value_col].round(2)
    return out


def share_by_group(df: pd.DataFrame, group: str, metric: str) -> pd.DataFrame:
    out = group_aggregate(df, group, metric, "sum", ascending=False)
    value_col = out.columns[1]
    total = out[value_col].sum()
    if total <= 0:
        raise AnalysisError("Percentage shares cannot be computed because the total is not positive.")
    out["Share %"] = (out[value_col] / total * 100).round(2)
    return out


def trend_over_time(df: pd.DataFrame, date_col: str, metric: str, grain: str = "month") -> pd.DataFrame:
    d = df.dropna(subset=[date_col, metric])
    if d.empty:
        raise AnalysisError("There are no rows with both a valid date and a valid value.")
    d = d.assign(Period=period_labels(d, date_col, grain))
    value_col = f"Total {metric}"
    out = d.groupby("Period")[metric].sum().reset_index(name=value_col)
    out[value_col] = out[value_col].round(2)
    change = out[value_col].pct_change().replace([np.inf, -np.inf], np.nan) * 100
    out["Change %"] = change.round(2)
    return out


def describe_numeric(df: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    stats = df[numeric_cols].describe().T.reset_index().rename(columns={"index": "Column"})
    return stats.round(2)


def find_outliers(df: pd.DataFrame, metric: str) -> tuple[pd.DataFrame, dict]:
    values = df[metric].dropna()
    if len(values) < 4:
        raise AnalysisError("There are too few values to look for unusual data points.")
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    mask = (df[metric] < lower) | (df[metric] > upper)
    rows = df[mask].copy()
    rows = rows.reindex(rows[metric].sub(values.median()).abs().sort_values(ascending=False).index)
    stats = {
        "lower": float(lower),
        "upper": float(upper),
        "count": int(mask.sum()),
        "pct": float(mask.sum() / len(values) * 100),
        "median": float(values.median()),
    }
    return rows, stats

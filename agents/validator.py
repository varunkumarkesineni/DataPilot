"""Result Validator: checks the plan before running and the result after."""
from __future__ import annotations

import numpy as np
import pandas as pd

from agents.question_analyzer import TIME_GROUPS, GROUP_INTENTS

NEEDS_METRIC = {"scalar", "group_aggregate", "share", "trend", "outliers", "insights"}


def validate_plan(plan: dict, df: pd.DataFrame) -> tuple[bool, str]:
    p = plan["params"]
    if plan["intent"] == "unsupported" or plan["tool"] is None:
        return False, ("The question does not match a supported analysis "
                       "(totals, averages, rankings, shares, trends, unusual values, statistics, insights).")

    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if p.get("missing_metric"):
        return False, (f"The question refers to '{p['missing_metric']}', which is not a column in this dataset. "
                       f"Numeric columns available: {', '.join(numeric) or 'none'}.")
    if plan["tool"] == "describe":
        return (True, "Plan is valid: numeric columns found.") if numeric else (False, "The dataset has no numeric columns.")

    if plan["tool"] in NEEDS_METRIC:
        metric = p.get("metric")
        if not metric or metric not in df.columns:
            return False, f"No suitable numeric column found. Numeric columns available: {', '.join(numeric) or 'none'}."
        if metric not in numeric:
            return False, f"Column '{metric}' is not numeric."
        if df[metric].notna().sum() == 0:
            return False, f"Column '{metric}' has no valid numeric values."

    if plan["tool"] in ("group_aggregate", "share"):
        group = p.get("group_by")
        if not group:
            kind = "a category column or time period" if plan["intent"] in GROUP_INTENTS else "a grouping column"
            return False, f"This question needs {kind} to group by, but none could be identified."
        if group in TIME_GROUPS and not p.get("date_column"):
            return False, "This question needs a date column, but the dataset has none."
        if group not in TIME_GROUPS and group not in df.columns:
            return False, f"Column '{group}' was not found."
    if plan["tool"] == "trend" and not p.get("date_column"):
        return False, "A trend needs a date column, but the dataset has none."
    for f in p.get("filters") or []:
        if f["column"] not in df.columns:
            return False, f"Filter column '{f['column']}' was not found."
    return True, f"Plan is valid: required columns exist ({p.get('metric') or 'numeric columns'})."


def validate_result(result: dict) -> tuple[bool, str]:
    if not result.get("summary"):
        return False, "The analysis produced no result."
    for label, value in result.get("metrics", {}).items():
        if isinstance(value, (int, float, np.integer, np.floating)) and not np.isfinite(value):
            return False, f"The metric '{label}' is not a finite number."
    for title, table in result.get("tables", []):
        if table is None or table.empty:
            return False, f"The table '{title}' is empty."
    return True, (f"Result verified: {len(result.get('metrics', {}))} metrics and "
                  f"{len(result.get('tables', []))} table(s) contain valid values.")

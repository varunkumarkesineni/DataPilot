"""Analysis Planner: chooses which Pandas tool to run and describes the plan."""
from __future__ import annotations

from agents.question_analyzer import TIME_GROUPS
from utils.data_analysis import AGG_LABEL


def _gname(group: str | None) -> str:
    return TIME_GROUPS[group][0] if group in TIME_GROUPS else (group or "")


def build_plan(u: dict, profile: dict) -> dict:
    intent, metric, group = u["intent"], u.get("metric"), u.get("group_by")
    agg = u.get("agg") or "sum"
    date_col = profile["datetime"][0] if profile["datetime"] else None
    params = {
        "metric": metric,
        "missing_metric": u.get("missing_metric"),
        "filters": u.get("filters") or [],
        "date_column": date_col,
    }
    agg_word = AGG_LABEL.get(agg, "Total")
    desc_steps: list[str] = []
    tool = None

    if intent in ("total", "average"):
        agg = "mean" if intent == "average" else "sum"
        agg_word = AGG_LABEL[agg]
        if group:
            tool = "group_aggregate"
            params.update(group_by=group, agg=agg, ascending=False, top_n=None)
            desc_steps = [f"Group by {_gname(group)}", f"{agg_word} {metric}", "Sort descending"]
        else:
            tool = "scalar"
            params.update(agg=agg)
            desc_steps = [f"{agg_word} of {metric}"]
    elif intent in ("top_group", "ranking"):
        tool = "group_aggregate"
        asc = bool(u.get("ascending"))
        params.update(group_by=group, agg=agg, ascending=asc, top_n=u.get("top_n"))
        desc_steps = [f"Group by {_gname(group)}", f"{agg_word} {metric}",
                      f"Sort {'ascending' if asc else 'descending'}"]
        desc_steps.append(f"Keep top {u['top_n']}" if intent == "ranking" else "Take the first row")
    elif intent == "share":
        tool = "share"
        params.update(group_by=group)
        desc_steps = [f"Group by {_gname(group)}", f"Sum {metric}", "Divide by overall total × 100"]
    elif intent == "trend":
        tool = "trend"
        params.update(grain="month")
        desc_steps = [f"Group {metric} by month", "Sum per month", "Compare consecutive months"]
    elif intent == "outliers":
        tool = "outliers"
        desc_steps = [f"Compute IQR bounds for {metric}", "Flag values outside 1.5 × IQR"]
    elif intent == "describe":
        tool = "describe"
        desc_steps = ["Compute count, mean, std, min, quartiles and max for numeric columns"]
    elif intent == "insights":
        tool = "insights"
        desc_steps = [f"Total and average {metric}", "Share by main categories",
                      "Monthly trend (if a date column exists)", "Outlier check"]
    else:
        desc_steps = ["No safe analysis found for this question"]

    if params["filters"]:
        flt = ", ".join(f"{f['column']} = {f['value']}" for f in params["filters"])
        desc_steps.insert(0, f"Filter {flt}")

    return {
        "intent": intent,
        "tool": tool,
        "params": params,
        "understood": u["understood"],
        "source": u.get("source", "rules"),
        "description": " → ".join(desc_steps),
    }

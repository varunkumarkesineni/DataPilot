"""Question Analyzer: turns a natural-language question into a structured request.

Gemini is used to understand the question when available. A deterministic rule-based
analyzer is the fallback, so the app still works without an API key.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from services.gemini_service import GeminiError

INTENTS = (
    "total", "average", "top_group", "ranking", "share",
    "trend", "outliers", "describe", "insights", "unsupported",
)
GROUP_INTENTS = {"top_group", "ranking", "share"}
TIME_GROUPS = {"__month__": ("Month", "month"), "__year__": ("Year", "year")}

_OUTLIER = ("unusual", "outlier", "anomal", "abnormal", "strange", "suspicious")
_HIGH = ("highest", "best", "most", "largest", "biggest", "maximum", "greatest", "peak", "max")
_LOW = ("lowest", "worst", "least", "smallest", "minimum", "weakest", "poorest", "min")
_MISSING_METRIC_WORDS = ("profit", "margin", "cost", "expense", "discount", "salary", "cogs", "tax")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


# ----------------------------------------------------------------------------- public
def analyze(question: str, df: pd.DataFrame, profile: dict, gemini=None) -> dict:
    result, source = None, "rules"
    if gemini is not None and getattr(gemini, "available", False):
        try:
            result = _analyze_with_gemini(question, df, profile, gemini)
            source = "gemini"
        except (GeminiError, ValueError, KeyError, TypeError):
            result = None
    if result is None:
        result = _analyze_with_rules(question, df, profile)
    result["source"] = source
    result["understood"] = _describe(result)
    return result


# ----------------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    return text.lower().replace("_", " ")


def _has(ql: str, words) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", ql) for w in words)


def _variants(col: str) -> set[str]:
    n = _norm(col)
    v = {n, n + "s", n + "es"}
    if n.endswith("y"):
        v.add(n[:-1] + "ies")
    return v


def _find(ql: str, col: str) -> int:
    positions = [m.start() for v in _variants(col) for m in re.finditer(rf"\b{re.escape(v)}\b", ql)]
    return min(positions) if positions else -1


def _default_metric(numeric: list[str]) -> str | None:
    for key in ("revenue", "sales", "amount", "income", "total"):
        for col in numeric:
            if key in col.lower():
                return col
    return numeric[0] if numeric else None


def _pick_metric(ql: str, profile: dict):
    """Return (metric, missing_word). missing_word is set if the question names a
    measure (e.g. 'profit') that has no matching numeric column."""
    nums = profile["numeric"]
    if not nums:
        return None, None
    for col in nums:
        if _find(ql, col) >= 0:
            return col, None

    missing = None
    families = [
        (("sales", "revenue", "income", "turnover"), ("revenue", "sales", "amount", "total")),
        (("quantity", "units", "qty"), ("quantity", "units", "qty")),
        (("price",), ("price",)),
    ]
    for triggers, candidates in families:
        hit = next((t for t in triggers if re.search(rf"\b{t}", ql)), None)
        if not hit:
            continue
        for cand in candidates:
            for col in nums:
                if cand in col.lower():
                    return col, None
        missing = missing or hit
    missing = missing or next((w for w in _MISSING_METRIC_WORDS if re.search(rf"\b{w}", ql)), None)
    return _default_metric(nums), missing


def _pick_group(ql: str, profile: dict, intent: str):
    wants_group = intent in GROUP_INTENTS or bool(re.search(r"\b(per|by|each|across)\b", ql))
    if not wants_group:
        return None
    found = sorted((_find(ql, c), c) for c in profile["categorical"] if _find(ql, c) >= 0)
    if found:
        for pos, col in found:  # prefer a column introduced by by/per/each/across
            if re.search(r"(by|per|each|across)\s+(\w+\s+)?$", ql[:pos]):
                return col
        if len(found) > 1 and re.search(
            rf"{re.escape(_norm(found[0][1]))}s?\s+{re.escape(_norm(found[1][1]))}", ql
        ):
            return found[1][1]  # "product category" -> head noun is the last word
        return found[0][1]
    if profile["datetime"]:
        if re.search(r"\bmonth", ql):
            return "__month__"
        if re.search(r"\byear", ql):
            return "__year__"
    return None


def _find_filters(ql: str, df: pd.DataFrame, profile: dict, group: str | None) -> list[dict]:
    filters = []
    for col in profile["categorical"]:
        if col == group:
            continue
        uniques = df[col].dropna().astype(str).unique()
        if len(uniques) > 50:
            continue
        for val in uniques:
            if re.search(rf"\b{re.escape(val.lower())}\b", ql):
                filters.append({"column": col, "value": val})
                break
    return filters


def _top_n(ql: str):
    m = re.search(r"\b(?:top|bottom|best|worst|highest|lowest)\s+(\d+|" + "|".join(_NUMBER_WORDS) + r")\b", ql)
    if m:
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _NUMBER_WORDS[tok]
    return None


def _detect_intent(ql: str) -> str:
    if _has(ql, _OUTLIER):
        return "outliers"
    if _has(ql, ("insight", "recommend", "overview", "key takeaway")):
        return "insights"
    if _has(ql, ("trend", "over time", "growth", "month over month", "month-over-month", "seasonal")):
        return "trend"
    if _has(ql, ("describe", "statistic", "summary", "summarize", "summarise", "distribution")):
        return "describe"
    if _has(ql, ("percent", "share", "proportion", "contribution", "breakdown")):
        return "share"
    if re.search(r"\b(top|bottom)\b", ql):
        if _top_n(ql) is None and re.search(r"\bwhich\b", ql):
            return "top_group"
        return "ranking"
    if _has(ql, _HIGH + _LOW):
        return "top_group"
    if _has(ql, ("average", "mean", "avg")):
        return "average"
    if _has(ql, ("total", "sum", "overall", "how much")):
        return "total"
    return "unsupported"


# ----------------------------------------------------------------------------- rules
def _analyze_with_rules(question: str, df: pd.DataFrame, profile: dict) -> dict:
    ql = _norm(question.strip())
    intent = _detect_intent(ql)
    metric, missing = _pick_metric(ql, profile)
    group = _pick_group(ql, profile, intent)
    ascending = _has(ql, _LOW) or bool(re.search(r"\bbottom\b", ql))
    return {
        "intent": intent,
        "metric": metric,
        "missing_metric": missing,
        "group_by": group,
        "agg": "mean" if _has(ql, ("average", "mean", "avg")) else "sum",
        "ascending": ascending,
        "top_n": (_top_n(ql) or 5) if intent == "ranking" else None,
        "filters": _find_filters(ql, df, profile, group) if intent != "unsupported" else [],
    }


# ----------------------------------------------------------------------------- gemini
def _analyze_with_gemini(question: str, df: pd.DataFrame, profile: dict, gemini) -> dict:
    lines = []
    for col in df.columns:
        if col in profile["numeric"]:
            lines.append(f"- {col} (numeric)")
        elif col in profile["datetime"]:
            lines.append(f"- {col} (datetime)")
        else:
            vals = df[col].dropna().astype(str).unique()[:8]
            lines.append(f"- {col} (text; example values: {', '.join(vals)})")
    prompt = (
        "You are the Question Analyzer of a data-analysis agent. Convert the user's question "
        "into a JSON analysis request. You do NOT answer the question.\n\n"
        "Dataset columns:\n" + "\n".join(lines) + "\n\n"
        "Allowed intents: " + ", ".join(INTENTS) + "\n"
        "- total / average: one overall number (add group_by if the user wants it per group)\n"
        "- top_group: which group is highest or lowest\n"
        "- ranking: top or bottom N groups\n"
        "- share: percentage contribution of each group\n"
        "- trend: change over time; outliers: unusual values; describe: descriptive statistics\n"
        "- insights: general business insights about the dataset\n"
        "- unsupported: cannot be answered from these columns with the operations above "
        "(forecasts, causes, external data, columns that do not exist)\n\n"
        "Rules:\n"
        "- Use column names exactly as listed.\n"
        '- "metric" must be a numeric column, or null.\n'
        '- "group_by" must be a text column, or "__month__" / "__year__" (only if a datetime column exists), or null.\n'
        '- "agg" is "sum" or "mean". "order" is "desc" or "asc". "top_n" is an integer or null.\n'
        '- "filters": list of {"column", "value"} equality filters on text columns, only when the '
        "question restricts the data to a specific value.\n"
        'Return ONLY JSON: {"intent":..., "metric":..., "group_by":..., "agg":..., "order":..., '
        '"top_n":..., "filters":[...]}\n\n'
        f"Question: {json.dumps(question)}"
    )
    raw = gemini.generate_json(prompt)

    intent = raw.get("intent")
    if intent not in INTENTS:
        raise ValueError("unknown intent")
    metric = raw.get("metric")
    if metric not in profile["numeric"]:
        metric = _default_metric(profile["numeric"])
    group = raw.get("group_by")
    valid_groups = set(profile["categorical"]) | (set(TIME_GROUPS) if profile["datetime"] else set())
    group = group if group in valid_groups else None
    agg = raw.get("agg") if raw.get("agg") in ("sum", "mean") else "sum"
    top_n = raw.get("top_n")
    top_n = max(1, min(int(top_n), 50)) if isinstance(top_n, (int, float)) and top_n else None
    if intent == "ranking" and top_n is None:
        top_n = 5

    filters = []
    for f in raw.get("filters") or []:
        col, val = f.get("column"), f.get("value")
        if col in profile["categorical"] and val is not None:
            matches = [u for u in df[col].dropna().astype(str).unique() if u.lower() == str(val).lower()]
            if matches:
                filters.append({"column": col, "value": matches[0]})
    return {
        "intent": intent,
        "metric": metric,
        "missing_metric": None,
        "group_by": group,
        "agg": agg,
        "ascending": raw.get("order") == "asc",
        "top_n": top_n,
        "filters": filters,
    }


def _group_name(group: str | None) -> str:
    return TIME_GROUPS[group][0] if group in TIME_GROUPS else (group or "group")


def _describe(u: dict) -> str:
    metric, group = u.get("metric"), _group_name(u.get("group_by"))
    intent = u["intent"]
    text = {
        "total": f"Calculate the total of {metric}.",
        "average": f"Calculate the average of {metric}.",
        "top_group": f"Find the {group} with the {'lowest' if u.get('ascending') else 'highest'} {metric}.",
        "ranking": f"Rank {group} by {metric} and return the {'bottom' if u.get('ascending') else 'top'} {u.get('top_n')}.",
        "share": f"Work out each {group}'s percentage share of {metric}.",
        "trend": f"Analyse how {metric} changes over time.",
        "outliers": f"Look for unusual values in {metric}.",
        "describe": "Summarise the numeric columns with descriptive statistics.",
        "insights": f"Give a business overview of the dataset, focused on {metric}.",
        "unsupported": "The question does not map to a supported analysis.",
    }[intent]
    if u.get("group_by") and intent in ("total", "average"):
        text = text[:-1] + f" for each {group}."
    return text

"""AI Explanation: Gemini explains results that Pandas already computed.

Gemini never calculates. If it is unavailable, a deterministic explanation built from
the computed result is returned instead.
"""
from __future__ import annotations

import json

from services.gemini_service import GeminiError


def _payload(result: dict) -> dict:
    return {
        "summary": result["summary"],
        "metrics": {k: (v if isinstance(v, str) else float(v)) for k, v in result["metrics"].items()},
        "tables": {t: tbl.head(15).round(2).to_dict("records") for t, tbl in result["tables"]},
        "highlights": result.get("highlights", []),
    }


def explain(question: str, plan: dict, result: dict, gemini=None) -> dict:
    fallback = {
        "answer": result["summary"],
        "insights": result.get("highlights", [])[:4],
        "recommendation": "",
        "ai_used": False,
        "note": "",
    }
    if gemini is None or not getattr(gemini, "available", False):
        fallback["note"] = ("Gemini is not configured (GEMINI_API_KEY missing), so this is the computed summary "
                            "without an AI explanation.")
        return fallback

    prompt = (
        "You are the explanation layer of a data-analysis agent. A Python/Pandas tool has ALREADY computed "
        "the results below. Your only job is to explain them.\n\n"
        "Strict rules:\n"
        "- Use ONLY numbers that appear in the computed results. Never calculate, estimate or invent numbers.\n"
        "- If the results do not support a conclusion, do not state it.\n"
        "- Write for a business reader. Be concise and specific.\n\n"
        'Return ONLY JSON: {"answer": "1-3 sentence direct answer to the question", '
        '"insights": ["2 to 4 short insights"], '
        '"recommendation": "1-2 sentence practical recommendation, or an empty string if not appropriate"}\n\n'
        f"User question: {json.dumps(question)}\n"
        f"Analysis performed: {plan['description']}\n"
        f"Computed results: {json.dumps(_payload(result), default=str)}"
    )
    try:
        raw = gemini.generate_json(prompt)
        answer = str(raw.get("answer", "")).strip()
        insights = [str(i).strip() for i in (raw.get("insights") or []) if str(i).strip()][:4]
        if not answer:
            raise ValueError("empty answer")
        return {
            "answer": answer,
            "insights": insights or fallback["insights"],
            "recommendation": str(raw.get("recommendation") or "").strip(),
            "ai_used": True,
            "note": "",
        }
    except (GeminiError, ValueError, TypeError, AttributeError) as exc:
        reason = str(exc) if isinstance(exc, GeminiError) else "Gemini returned an unusable response."
        fallback["note"] = f"{reason} Showing the computed summary instead."
        return fallback

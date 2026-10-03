"""Analysis history stored in Firebase Firestore (Google Cloud Firestore client)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

COLLECTION = "analysis_history"
_client = None


class FirestoreError(Exception):
    """Raised for any Firestore failure. Messages never contain credentials."""


def _get_client():
    global _client
    if _client is None:
        try:
            from google.cloud import firestore

            kwargs = {}
            if os.environ.get("GOOGLE_CLOUD_PROJECT"):
                kwargs["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
            if os.environ.get("FIRESTORE_DATABASE"):
                kwargs["database"] = os.environ["FIRESTORE_DATABASE"]
            _client = firestore.Client(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise FirestoreError(
                "Firestore is not configured. Set up Google Cloud credentials to enable saved history."
            ) from exc
    return _client


def save_analysis(record: dict) -> None:
    """Store one analysis. Expected keys: user_question, dataset_name, analysis_type,
    answer, insights, recommendation. A UTC timestamp is added automatically."""
    doc = {
        "timestamp": datetime.now(timezone.utc),
        "user_question": record["user_question"],
        "dataset_name": record["dataset_name"],
        "analysis_type": record["analysis_type"],
        "answer": record["answer"],
        "insights": list(record.get("insights", [])),
        "recommendation": record.get("recommendation", ""),
    }
    try:
        _get_client().collection(COLLECTION).add(doc)
    except FirestoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FirestoreError(f"Could not save to Firestore ({type(exc).__name__}).") from exc


def get_history(limit: int = 50) -> list[dict]:
    """Most recent analyses first."""
    try:
        from google.cloud import firestore

        query = (
            _get_client()
            .collection(COLLECTION)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [{**d.to_dict(), "id": d.id} for d in query.stream()]
    except FirestoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FirestoreError(f"Could not read history from Firestore ({type(exc).__name__}).") from exc


def clear_history() -> None:
    """Delete every document in the history collection."""
    try:
        client = _get_client()
        while True:
            docs = list(client.collection(COLLECTION).limit(200).stream())
            if not docs:
                break
            batch = client.batch()
            for d in docs:
                batch.delete(d.reference)
            batch.commit()
    except FirestoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FirestoreError(f"Could not clear Firestore history ({type(exc).__name__}).") from exc

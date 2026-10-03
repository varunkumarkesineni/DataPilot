"""CSV loading, validation and light type inference."""
from __future__ import annotations

import io
import re
import warnings

import numpy as np
import pandas as pd
from pandas.api import types as pdt

MAX_ROWS = 200_000
MIN_PARSE_RATIO = 0.8


class DataLoadError(Exception):
    """Raised when an uploaded CSV cannot be used."""


def load_csv(data: bytes) -> pd.DataFrame:
    """Parse raw CSV bytes into a DataFrame with sensible dtypes."""
    if not data or not data.strip():
        raise DataLoadError("The uploaded CSV file is empty.")

    df = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(data), encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            raise DataLoadError("The uploaded CSV file is empty.") from None
        except pd.errors.ParserError:
            raise DataLoadError(
                "The file could not be parsed as a CSV. Please check its format."
            ) from None
        except Exception:
            raise DataLoadError("The file could not be read as a CSV.") from None
    if df is None:
        raise DataLoadError("The file encoding is not supported. Please save it as UTF-8.")

    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty or df.shape[1] == 0:
        raise DataLoadError("The CSV contains no usable data rows.")

    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise DataLoadError("The CSV has duplicate column names. Please rename them and retry.")

    return _infer_types(df.head(MAX_ROWS).reset_index(drop=True))


def _to_datetime(series: pd.Series) -> pd.Series:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(series, errors="coerce")


def _to_number(value) -> float:
    if pd.isna(value):
        return np.nan
    text = re.sub(r"[,$₹€£%\s]", "", str(value))
    try:
        return float(text)
    except ValueError:
        return np.nan


def _infer_types(df: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns that are really dates or numbers."""
    df = df.copy()
    for col in df.columns:
        series = df[col]
        if (
            pdt.is_numeric_dtype(series)
            or pdt.is_datetime64_any_dtype(series)
            or pdt.is_bool_dtype(series)
        ):
            continue
        non_null = series.dropna()
        if non_null.empty:
            continue

        iso_like = non_null.astype(str).str.match(r"^\d{4}-\d{1,2}-\d{1,2}").mean() > 0.8
        if re.search(r"date|time", col, re.I) or iso_like:
            parsed = _to_datetime(series)
            if parsed.notna().sum() >= MIN_PARSE_RATIO * len(non_null):
                df[col] = parsed
                continue

        numeric = series.map(_to_number)
        if numeric.notna().sum() >= MIN_PARSE_RATIO * len(non_null):
            df[col] = numeric.astype("float64")
    return df


def profile_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    """Group column names by kind: numeric, datetime, categorical."""
    numeric, datetime_cols, categorical = [], [], []
    for col in df.columns:
        s = df[col]
        if pdt.is_bool_dtype(s):
            categorical.append(col)
        elif pdt.is_numeric_dtype(s):
            numeric.append(col)
        elif pdt.is_datetime64_any_dtype(s):
            datetime_cols.append(col)
        else:
            categorical.append(col)
    return {"numeric": numeric, "datetime": datetime_cols, "categorical": categorical}


def dataset_info(df: pd.DataFrame) -> dict:
    """Basic facts shown after upload."""
    missing = df.isna().sum()
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "column_names": list(df.columns),
        "missing_total": int(missing.sum()),
        "missing_by_column": {c: int(n) for c, n in missing.items() if n > 0},
    }

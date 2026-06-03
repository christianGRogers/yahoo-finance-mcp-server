"""Helpers for turning yfinance return values (pandas, numpy, datetimes) into
JSON-serializable Python primitives that an MCP client / LLM can consume."""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

import numpy as np
import pandas as pd


def _clean_scalar(value: Any) -> Any:
    """Convert a single scalar value into something JSON-safe."""
    # pandas/numpy missing values
    try:
        if value is None:
            return None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    except (TypeError, ValueError):
        pass

    # numpy scalar types
    if isinstance(value, np.generic):
        value = value.item()

    # Re-check for NaN/inf after unwrapping numpy floats
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, (np.bool_, bool)):
        return bool(value)

    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)

    if isinstance(value, _dt.timedelta):
        return value.total_seconds()

    if isinstance(value, pd.Timedelta):
        return value.isoformat()

    if isinstance(value, (int, str)):
        return value

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)

    return value


def _index_to_key(idx: Any) -> str:
    """Render a DataFrame/Series index label as a string key."""
    if isinstance(idx, (pd.Timestamp, _dt.datetime, _dt.date)):
        try:
            return idx.isoformat()
        except Exception:
            return str(idx)
    if isinstance(idx, tuple):
        return " | ".join(str(_index_to_key(p)) for p in idx)
    return str(idx)


def to_jsonable(obj: Any, *, date_index_orient: bool = True) -> Any:
    """Recursively convert pandas/numpy/datetime structures to JSON-safe data.

    DataFrames become a dict keyed by (stringified) index -> {column: value}.
    Series become a dict of {index: value}. This keeps the row labels (often
    dates or field names) which are meaningful in Yahoo Finance data.
    """
    if obj is None:
        return None

    if isinstance(obj, pd.DataFrame):
        if obj.empty:
            return {}
        df = obj.copy()
        # Stringify the index so dates/tuples become keys.
        result: dict[str, Any] = {}
        for idx, row in df.iterrows():
            key = _index_to_key(idx)
            result[key] = {
                str(_index_to_key(col)): _clean_scalar(val)
                for col, val in row.items()
            }
        return result

    if isinstance(obj, pd.Series):
        if obj.empty:
            return {}
        return {
            _index_to_key(idx): _clean_scalar(val) for idx, val in obj.items()
        }

    if isinstance(obj, pd.Index):
        return [_clean_scalar(v) for v in obj.tolist()]

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]

    if isinstance(obj, np.ndarray):
        return [to_jsonable(v) for v in obj.tolist()]

    return _clean_scalar(obj)

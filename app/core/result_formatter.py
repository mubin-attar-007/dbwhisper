"""Helpers to format SQL agent responses."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def format_results(
    dataframe: pd.DataFrame | None,
    sql: str,
    output_format: str,
    execution_time_ms: float | None = None,
    page: int | None = None,
    page_size: int | None = None,
    has_next: bool | None = None,
    total_rows: int | None = None,
) -> dict[str, Any]:
    """Format query execution output for the API response."""

    if dataframe is None:
        return {
            "status": "error",
            "data": {},
            "message": "No data returned",
        }

    if output_format == "table":
        payload = dataframe.to_string(index=False)
    elif output_format == "csv":
        payload = dataframe.to_csv(index=False)
    else:
        payload = dataframe.to_dict(orient="records")

    csv_payload = dataframe.to_csv(index=False)
    raw_json_payload = dataframe.to_json(orient="records", date_format="iso")

    describe_df = dataframe.describe(include="all")
    describe_summary: dict[str, dict[str, Any]] = {}
    describe_text = ""
    if not describe_df.empty:
        describe_text = describe_df.to_string()
        describe_summary = {
            column: {metric: _serialize_value(value) for metric, value in metrics.items()}
            for column, metrics in describe_df.transpose().to_dict().items()
        }

    data_payload = {
        "status": "success",
        "data": {
            "results": payload,
            "sql": sql,
            "row_count": len(dataframe.index),
            "execution_time_ms": execution_time_ms,
            "csv": csv_payload,
            "raw_json": raw_json_payload,
            "describe": describe_summary,
            "describe_text": describe_text,
        },
    }

    # Include pagination metadata if present
    if page is not None:
        data_payload["data"].update({"page": page})
    if page_size is not None:
        data_payload["data"].update({"page_size": page_size})
    if has_next is not None:
        data_payload["data"].update({"has_next": has_next})
    if total_rows is not None:
        data_payload["data"].update({"total_rows": total_rows})

    return data_payload

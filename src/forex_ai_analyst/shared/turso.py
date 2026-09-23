"""Thin Turso (libSQL HTTP) client: typed statement execution only.

Every table this app uses (signal_candidates, execution_incidents,
runtime_controls, knowledge_documents, ...) is owned and migrated by its own
module via _execute()/_rows_as_dicts(); this module deliberately has no
domain tables or business logic of its own.
"""
import os

import requests

TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]  # libsql://<name>.turso.io
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

_PIPELINE_URL = TURSO_DATABASE_URL.replace("libsql://", "https://") + "/v2/pipeline"


def _typed_arg(value):
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _cell_value(cell: dict):
    if cell["type"] == "null":
        return None
    if cell["type"] == "integer":
        return int(cell["value"])
    if cell["type"] == "float":
        return float(cell["value"])
    return cell["value"]


def _execute(sql: str, args: list | None = None) -> dict:
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [_typed_arg(a) for a in args]

    response = requests.post(
        _PIPELINE_URL,
        headers={"Authorization": f"Bearer {TURSO_AUTH_TOKEN}"},
        json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        timeout=15,
    )
    response.raise_for_status()
    result = response.json()["results"][0]
    if result["type"] == "error":
        raise RuntimeError(f"Turso query failed: {result.get('error')}")
    return result["response"]["result"]


def _rows_as_dicts(result: dict) -> list[dict]:
    col_names = [c["name"] for c in result["cols"]]
    return [dict(zip(col_names, (_cell_value(cell) for cell in row))) for row in result["rows"]]

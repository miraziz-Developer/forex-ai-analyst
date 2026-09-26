"""Immutable hypothesis registry and append-only research ledger.

- A manifest file is written once. Re-registering identical content is a no-op;
  different content under the same hypothesis_id is refused: open a new version.
- Status lives in an append-only ledger (never by editing the manifest):
  REGISTERED, TRIAL, HOLDOUT_OPENED, VERDICT. Failed experiments stay recorded.
- The holdout can be opened exactly once per hypothesis version.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.research.edge_lab.models import Verdict, content_hash

REQUIRED_KEYS = ("hypothesis_id", "economic_thesis", "markets", "directions", "feature_version",
                 "discovery_period", "validation_period", "holdout_period", "parameter_grid",
                 "max_trials_per_direction", "cost_model", "promotion_gate")


class RegistryError(RuntimeError):
    pass


def root() -> Path:
    return Path(os.environ.get("EDGE_LAB_ROOT", "research_output/edge_lab"))


def grid_size(grid: dict) -> int:
    size = 1
    for values in grid.values():
        size *= len(values)
    return size


def validation_error(manifest: dict) -> str | None:
    missing = [k for k in REQUIRED_KEYS if k not in manifest]
    if missing:
        return f"missing keys: {', '.join(missing)}"
    periods = [manifest["discovery_period"], manifest["validation_period"], manifest["holdout_period"]]
    if any(len(p) != 2 or p[0] >= p[1] for p in periods):
        return "every period must be [start, end) with start < end"
    if any(a[1] > b[0] for a, b in zip(periods, periods[1:])):
        return "periods must be ordered discovery < validation < holdout without overlap"
    if grid_size(manifest["parameter_grid"]) > manifest["max_trials_per_direction"]:
        return "parameter grid exceeds max_trials_per_direction"
    if not manifest["markets"] or not manifest["directions"]:
        return "markets and directions must be non-empty"
    return None


def manifest_path(hypothesis_id: str) -> Path:
    return root() / "hypotheses" / f"{hypothesis_id}.json"


def ledger_path() -> Path:
    return root() / "manifests" / "ledger.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(event: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")


def ledger(hypothesis_id: str | None = None) -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in events if hypothesis_id is None or e.get("hypothesis_id") == hypothesis_id]


def register(manifest: dict) -> str:
    """Write the manifest once and return its content hash."""
    error = validation_error(manifest)
    if error:
        raise RegistryError(error)
    digest = content_hash(manifest)
    path = manifest_path(manifest["hypothesis_id"])
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("manifest_hash") != digest:
            raise RegistryError(f"{manifest['hypothesis_id']} is already registered with different content; "
                                "open a new hypothesis version instead of editing it")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"manifest_hash": digest, "manifest": manifest}, indent=2, sort_keys=True,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    _append({"event": "REGISTERED", "hypothesis_id": manifest["hypothesis_id"], "manifest_hash": digest,
             "at": _now()})
    return digest


def load(hypothesis_id: str) -> dict:
    path = manifest_path(hypothesis_id)
    if not path.exists():
        raise RegistryError(f"{hypothesis_id} is not registered")
    stored = json.loads(path.read_text(encoding="utf-8"))
    if content_hash(stored["manifest"]) != stored["manifest_hash"]:
        raise RegistryError(f"{hypothesis_id} manifest was modified after registration")
    return stored["manifest"]


def record_trial(hypothesis_id: str, config: dict, result: dict) -> None:
    load(hypothesis_id)
    _append({"event": "TRIAL", "hypothesis_id": hypothesis_id, "config": config, "result": result, "at": _now()})


def trial_count(hypothesis_id: str) -> int:
    return sum(1 for e in ledger(hypothesis_id) if e["event"] == "TRIAL")


def open_holdout(hypothesis_id: str) -> dict:
    """Unseal the holdout period, once. Returns [start, end)."""
    manifest = load(hypothesis_id)
    if any(e["event"] == "HOLDOUT_OPENED" for e in ledger(hypothesis_id)):
        raise RegistryError(f"{hypothesis_id} holdout was already used; results now count as discovery data")
    _append({"event": "HOLDOUT_OPENED", "hypothesis_id": hypothesis_id,
             "manifest_hash": content_hash(manifest), "at": _now()})
    return {"start": manifest["holdout_period"][0], "end": manifest["holdout_period"][1]}


def supersede(old_id: str, new_id: str, reason: str) -> None:
    """Retire a version in favour of a registered successor; the old one stays on record."""
    load(old_id)
    load(new_id)
    _append({"event": "SUPERSEDED", "hypothesis_id": old_id, "successor": new_id, "reason": reason,
             "trials_before_supersede": trial_count(old_id), "at": _now()})


def record_verdict(hypothesis_id: str, verdict: Verdict, reasons: list[str]) -> None:
    load(hypothesis_id)
    _append({"event": "VERDICT", "hypothesis_id": hypothesis_id, "verdict": str(verdict),
             "reasons": reasons, "at": _now()})

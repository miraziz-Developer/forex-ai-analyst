"""Train, export and apply the FX logistic model used by the forward (paper) test.

Training (once a year, locally, needs the `research` extra):
    python3 -m forex_ai_analyst.forex.ml_model 2026
writes src/forex_ai_analyst/forex/models/fx_logistic_2026.json, trained on every
sample whose 5-day label ended before 2026-01-01 — the same rule as the
walk-forward study. Applying a model needs only the standard library.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models"


def train(year: int) -> Path:
    import numpy as np  # noqa: F401  (research extra)
    from forex_ai_analyst.forex.ml_study import LONG_T, SHORT_T, build_dataset, models

    X, y, meta, names = build_dataset()
    mask = np.array([m["exit_day"] < f"{year}-01-01" for m in meta])
    pipeline = models()["logistic"]()
    pipeline.fit(X[mask], y[mask])
    scaler, clf = pipeline[0], pipeline[1]
    payload = {"version": f"fx_logistic_{year}", "trained_through": f"{year - 1}-12-31", "samples": int(mask.sum()),
               "features": names, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
               "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
               "long_threshold": LONG_T, "short_threshold": SHORT_T, "horizon_days": 5,
               "created_at": datetime.now(timezone.utc).isoformat()}
    MODEL_DIR.mkdir(exist_ok=True)
    path = MODEL_DIR / f"fx_logistic_{year}.json"
    path.write_text(json.dumps(payload, indent=1) + "\n")
    return path


def load(year: int | None = None) -> dict:
    """The model for `year` (default: the newest file)."""
    files = sorted(MODEL_DIR.glob("fx_logistic_*.json"))
    if year is not None:
        files = [p for p in files if p.stem.endswith(str(year))]
    if not files:
        raise FileNotFoundError("no FX logistic model exported")
    return json.loads(files[-1].read_text())


def probability_up(model: dict, feats: dict) -> float:
    z = model["intercept"]
    for name, mean, scale, coef in zip(model["features"], model["mean"], model["scale"], model["coef"]):
        z += coef * (feats[name] - mean) / (scale or 1.0)
    return 1 / (1 + math.exp(-z))


if __name__ == "__main__":
    print(train(int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(timezone.utc).year))

"""Train, export and apply the FX logistic models used by the forward (demo) test.

Training (once a year, locally, needs the `research` extra):
    python3 -m forex_ai_analyst.forex.ml_model 2026                       # v1
    python3 -m forex_ai_analyst.forex.ml_model 2026 fx_logistic_cot_c01   # v2 + COT, C = 0.1
writes src/forex_ai_analyst/forex/models/<family>_<year>.json, trained on every
sample whose 5-day label ended before January 1 of that year — the same rule as
the walk-forward study. Applying a model needs only the standard library.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models"
FAMILIES = {
    "fx_logistic": {"uses_cot": False, "c": 1.0, "tp_atr": None},         # v1, docs/ML_STUDY.md
    # v2 + regularisation, docs/ML_IMPROVEMENTS_STUDY.md (C); no take-profit (T3 fails on corrected data)
    "fx_logistic_cot_c01": {"uses_cot": True, "c": 0.1, "tp_atr": None},
}


def train(year: int, family: str = "fx_logistic") -> Path:
    import numpy as np  # noqa: F401  (research extra)
    from forex_ai_analyst.forex.ml_study import LONG_T, SHORT_T, build_dataset, models

    spec = FAMILIES[family]
    X, y, meta, names = build_dataset(with_cot=spec["uses_cot"])
    mask = np.array([m["exit_day"] < f"{year}-01-01" for m in meta])
    pipeline = models(c=spec["c"])["logistic"]()
    pipeline.fit(X[mask], y[mask])
    scaler, clf = pipeline[0], pipeline[1]
    payload = {"version": f"{family}_{year}", "family": family, "uses_cot": spec["uses_cot"], "c": spec["c"],
               "trained_through": f"{year - 1}-12-31", "samples": int(mask.sum()),
               "features": names, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
               "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
               "long_threshold": LONG_T, "short_threshold": SHORT_T, "horizon_days": 5,
               "created_at": datetime.now(timezone.utc).isoformat()}
    MODEL_DIR.mkdir(exist_ok=True)
    path = MODEL_DIR / f"{family}_{year}.json"
    path.write_text(json.dumps(payload, indent=1) + "\n")
    return path


def load(family: str = "fx_logistic", year: int | None = None) -> dict:
    """The newest model of `family` (or the one for `year`)."""
    files = sorted(p for p in MODEL_DIR.glob(f"{family}_*.json") if p.stem[len(family) + 1:].isdigit())
    if year is not None:
        files = [p for p in files if p.stem.endswith(str(year))]
    if not files:
        raise FileNotFoundError(f"no exported model for {family}")
    model = json.loads(files[-1].read_text())
    model.setdefault("family", family)
    model.setdefault("uses_cot", False)
    return model


def load_all() -> list[dict]:
    """The newest model of every family that has been exported."""
    out = []
    for family in FAMILIES:
        try:
            out.append(load(family))
        except FileNotFoundError:
            continue
    return out


def probability_up(model: dict, feats: dict) -> float:
    z = model["intercept"]
    for name, mean, scale, coef in zip(model["features"], model["mean"], model["scale"], model["coef"]):
        z += coef * (feats[name] - mean) / (scale or 1.0)
    return 1 / (1 + math.exp(-z))


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(timezone.utc).year
    print(train(year, sys.argv[2] if len(sys.argv) > 2 else "fx_logistic"))

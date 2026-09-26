"""Edge lab CLI.

    python3 -m forex_ai_analyst.research.edge_lab.cli register --hypothesis crowding_exhaustion_v1
    python3 -m forex_ai_analyst.research.edge_lab.cli feasibility --hypothesis crowding_exhaustion_v1
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from forex_ai_analyst.research.edge_lab import data, quality, registry
from forex_ai_analyst.research.edge_lab.hypotheses import HYPOTHESES
from forex_ai_analyst.research.edge_lab.models import Verdict, content_hash


def _manifest(hypothesis_id: str) -> dict:
    if hypothesis_id not in HYPOTHESES:
        raise SystemExit(f"unknown hypothesis {hypothesis_id}; known: {', '.join(HYPOTHESES)}")
    return HYPOTHESES[hypothesis_id]


def cmd_register(args) -> None:
    digest = registry.register(_manifest(args.hypothesis))
    print(f"{args.hypothesis} registered: {digest}")


def cmd_feasibility(args) -> None:
    manifest = _manifest(args.hypothesis)
    report = data.feasibility(manifest["markets"], manifest["discovery_period"][0], manifest["holdout_period"][1])
    report["hypothesis_id"] = args.hypothesis
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    path = registry.root() / "manifests" / "data_sources_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, row in report["summary"].items():
        print(f"{name:18s} {row['declared']:20s} {row['pairs_available']}/{row['pairs_probed']} pairs")
    print(f"written: {path}")


def feature_code_hash() -> str:
    """Hash of the code that defines features, events and the event study (recorded with every trial)."""
    here = Path(__file__).parent
    files = [here / "features.py", here / "event_study.py", here / "hypotheses" / "crowding_exhaustion.py"]
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def _source_report(name, records, interval_ms, start, end, now_ms, ohlc=False, non_negative=()):
    report = quality.check_series(records, dataset=name, expected_interval_ms=interval_ms, now_ms=now_ms,
                                  start_ms=data._to_ms(start), end_ms=data._to_ms(end), ohlc=ohlc,
                                  non_negative=non_negative)
    normalized, _, _ = quality.normalize(records)
    digest = content_hash([[r.event_time_ms, r.values] for r in normalized])
    summary = {k: v for k, v in asdict(report).items() if k != "dataset"} | {"dataset_hash": digest}
    return normalized, summary


def cmd_events(args) -> None:
    from forex_ai_analyst.research.edge_lab import event_study, features
    from forex_ai_analyst.research.edge_lab.hypotheses import crowding_exhaustion as ce

    hid = args.hypothesis
    manifest = registry.load(hid)
    if any(e["event"] == "TRIAL" and e.get("config", {}).get("stage") == "event_study" for e in registry.ledger(hid)):
        raise SystemExit(f"{hid} event study already ran; its trials are in the ledger. Changes need a new version.")
    opened = any(e["event"] == "HOLDOUT_OPENED" for e in registry.ledger(hid))
    disc_start, disc_end = manifest["discovery_period"]
    load_start = (date.fromisoformat(disc_start) - timedelta(days=31)).isoformat()
    load_end = min((date.fromisoformat(disc_end) + timedelta(days=2)).isoformat(), data.data_end(manifest, opened))
    lo, hi = data._to_ms(disc_start), data._to_ms(disc_end)
    now_ms, code_hash = int(time.time() * 1000), feature_code_hash()
    configs = ce.event_configs()
    rows: dict[tuple, list[dict]] = {}
    quality_report = {"hypothesis_id": hid, "load_period": [load_start, load_end], "feature_code_hash": code_hash,
                      "pairs": {}}
    for pair in manifest["markets"]:
        print(f"loading {pair}", flush=True)
        perp, _ = data.load_klines("perp_klines_15m", pair, load_start, load_end, now_ms)
        spot, _ = data.load_klines("spot_klines_15m", pair, load_start, load_end, now_ms)
        premium, _ = data.load_klines("premium_index_15m", pair, load_start, load_end, now_ms)
        funding, _ = data.load_funding(pair, load_start, load_end, now_ms)
        oi, _ = data.load_open_interest(pair, load_start, load_end, now_ms)
        reports = {}
        perp, reports["perp_klines_15m"] = _source_report("perp", perp, data.FIFTEEN_MIN, load_start, load_end,
                                                          now_ms, True, ("volume", "quote_volume"))
        spot, reports["spot_klines_15m"] = _source_report("spot", spot, data.FIFTEEN_MIN, load_start, load_end,
                                                          now_ms, True, ("volume",))
        premium, reports["premium_index_15m"] = _source_report("premium", premium, data.FIFTEEN_MIN, load_start,
                                                               load_end, now_ms)
        funding, reports["funding"] = _source_report("funding", funding, 8 * data.HOUR, load_start, load_end, now_ms)
        oi, reports["open_interest_5m"] = _source_report("oi", oi, data.FIVE_MIN, load_start, load_end, now_ms,
                                                         non_negative=("oi",))
        quality_report["pairs"][pair] = reports
        frame = features.compute(features.align(pair, perp, spot, premium, funding, oi,
                                                feature_version=manifest.get("feature_version", "v1")))
        for direction in manifest["directions"]:
            for config in configs:
                key = (direction, tuple(sorted(config.items())))
                for event in ce.detect_events(frame, config, direction, hid):
                    if not lo <= event.decision_time_ms < hi:
                        continue
                    result = event_study.outcome(frame, event.features["bar_index"], direction)
                    if result is not None:
                        rows.setdefault(key, []).append({"pair": pair, "decision_time_ms": event.decision_time_ms,
                                                         "fingerprint": event.fingerprint, **result})
        del frame, perp, spot, premium, funding, oi

    out = registry.root() / "event_studies" / hid
    out.mkdir(parents=True, exist_ok=True)
    (registry.root() / "manifests" / f"data_quality_{hid}.json").write_text(
        json.dumps(quality_report, indent=2, default=str) + "\n", encoding="utf-8")
    cost = manifest["cost_model"]["base"]
    round_trip = 2 * (cost["taker_fee_pct_per_side"] + cost["slippage_pct_per_side"]) / 100
    gate = manifest["event_study_gate"]
    results, lines = [], [f"# Event study - {hid}", "", f"Discovery {disc_start}..{disc_end}; primary horizon "
                          f"{event_study.PRIMARY_HORIZON}; base round-trip cost {round_trip:.4%}; "
                          f"feature code {code_hash[:12]}.", "",
                          "| Direction | Config | Events | Pairs | Median 4h | Median 24h | P(median>0) | Verdict |",
                          "|---|---|---|---|---|---|---|---|"]
    for direction in manifest["directions"]:
        for config in configs:
            key = (direction, tuple(sorted(config.items())))
            summary = event_study.summarize(rows.get(key, []), gate, round_trip)
            config_record = {"stage": "event_study", "direction": direction, **config}
            registry.record_trial(hid, config_record, {"events": summary["events"], "gate": summary["gate"],
                                                       "primary_median": summary.get("horizons", {}).get(
                                                           event_study.PRIMARY_HORIZON, {}).get("median"),
                                                       "feature_code_hash": code_hash})
            results.append({"direction": direction, "config": config, "summary": summary})
            h = summary.get("horizons", {})
            lines.append(f"| {direction} | {', '.join(f'{k}={v}' for k, v in config.items())} | {summary['events']} | "
                         f"{len(summary['pairs'])} | {h.get('4h', {}).get('median', 0):+.4%} | "
                         f"{h.get('24h', {}).get('median', 0):+.4%} | "
                         f"{summary.get('bootstrap', {}).get('prob_positive')} | {summary['gate']['verdict']} |")
    for direction in manifest["directions"]:
        verdicts = [r["summary"]["gate"]["verdict"] for r in results if r["direction"] == direction]
        if "PASS" in verdicts:
            continue
        verdict = Verdict.INSUFFICIENT_EVIDENCE if all(v == "INSUFFICIENT_EVIDENCE" for v in verdicts) else Verdict.REJECTED
        registry.record_verdict(hid, verdict, [f"{direction}: no event-study configuration passed the gate"])
        lines.append(f"\n**{direction}: {verdict}** - no configuration passed the pre-registered event-study gate.")
    (out / "summary.json").write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (out / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["direction", "config", "pair", "decision_time_ms", "fingerprint", "ret_4h", "ret_24h",
                         "mfe", "mae"])
        for (direction, config_key), items in rows.items():
            for r in items:
                writer.writerow([direction, dict(config_key), r["pair"], r["decision_time_ms"], r["fingerprint"],
                                 r["ret_4h"], r["ret_24h"], r["mfe"], r["mae"]])
    print("\n".join(lines))


def cmd_supersede(args) -> None:
    registry.supersede(args.old, args.new, args.reason)
    print(f"{args.old} superseded by {args.new}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="forex-edge-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("register", cmd_register), ("feasibility", cmd_feasibility), ("events", cmd_events)):
        command = sub.add_parser(name)
        command.add_argument("--hypothesis", required=True)
        command.set_defaults(handler=handler)
    supersede = sub.add_parser("supersede")
    supersede.add_argument("--old", required=True)
    supersede.add_argument("--new", required=True)
    supersede.add_argument("--reason", required=True)
    supersede.set_defaults(handler=cmd_supersede)
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()

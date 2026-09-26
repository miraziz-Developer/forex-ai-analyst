"""Edge lab CLI.

    python3 -m forex_ai_analyst.research.edge_lab.cli register --hypothesis crowding_exhaustion_v1
    python3 -m forex_ai_analyst.research.edge_lab.cli feasibility --hypothesis crowding_exhaustion_v1
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from forex_ai_analyst.research.edge_lab import data, registry
from forex_ai_analyst.research.edge_lab.hypotheses import HYPOTHESES


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


def cmd_supersede(args) -> None:
    registry.supersede(args.old, args.new, args.reason)
    print(f"{args.old} superseded by {args.new}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="forex-edge-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("register", cmd_register), ("feasibility", cmd_feasibility)):
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

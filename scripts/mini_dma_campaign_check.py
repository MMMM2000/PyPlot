"""Validate a Mini DMA optimization campaign before live hardware work."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_logging.mini_dma_logger.campaign import check_result_to_json, validate_campaign


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a Mini DMA optimization campaign manifest.")
    parser.add_argument("manifest", help="Path to campaign.yaml or campaign.json.")
    parser.add_argument("--repo-root", default=".", help="Repository root for git checks.")
    parser.add_argument("--skip-git", action="store_true", help="Skip branch/commit/clean-worktree checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = validate_campaign(args.manifest, repo_root=Path(args.repo_root), skip_git=args.skip_git)
    if args.json:
        print(check_result_to_json(result))
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"Mini DMA campaign check: {status}")
        print(f"Campaign: {result.campaign_id}")
        print(f"Root: {result.campaign_root}")
        if result.derived.get("max_correction_travel_mm") is not None:
            print(f"Derived correction travel limit: {result.derived['max_correction_travel_mm']:.6g} mm")
        if result.derived.get("report_path"):
            print(f"Report: {result.derived['report_path']}")
        for warning in result.warnings:
            print(f"Warning: {warning}")
        for error in result.errors:
            print(f"Error: {error}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

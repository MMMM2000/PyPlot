"""Publish a validated database with an exact backup and stale-target protection."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microwire_data_builder.project_maintenance import publish_verified_project

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--expected-target-sha256', required=True)
    args = parser.parse_args()
    receipt = publish_verified_project(args.candidate, args.target, backup=args.backup,
                                       expected_target_sha256=args.expected_target_sha256)
    print(json.dumps(receipt))

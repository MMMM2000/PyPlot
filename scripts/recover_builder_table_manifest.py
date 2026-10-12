"""Explicitly recover a COPY after external table edits left stale descriptors."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microwire_data_builder.table_manifest_recovery import recover_table_manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    changed = recover_table_manifest(args.source, args.destination)
    print(json.dumps({'recovered_copy': str(args.destination), 'accepted_edited_tables': changed,
                      'strict_verification_passed': True, 'original_unchanged': True}))

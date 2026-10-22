"""Correct tables in a new Builder package, preserving deferred measurements."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microwire_data_builder.project_maintenance import update_project_tables

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('tables_json', type=Path, help='JSON mapping section names to columns/rows/index tables')
    args = parser.parse_args()
    tables = json.loads(args.tables_json.read_text(encoding='utf-8'))
    index = update_project_tables(args.source, args.destination, tables)
    print(json.dumps({'verified_copy': str(index.path), 'revision': index.manifest['revision']}))

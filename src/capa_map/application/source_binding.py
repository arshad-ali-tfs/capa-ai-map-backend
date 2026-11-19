"""Verify explicit source recovery for a copied run without changing its provenance."""
import csv
import hashlib
import json
from pathlib import Path

NARRATIVES = ('detailed_description', 'investigation', 'conclusion')
REQUIRED = {'pr_id', 'site', 'record_type', *NARRATIVES}


def read_binding(directory: Path, original_snapshot_id: str, metadata: dict):
    try:
        binding = json.loads((directory / 'source_binding.json').read_text(encoding='utf-8'))
        if (binding.get('schema_version') == 1
                and binding.get('analysis_snapshot_id') == original_snapshot_id
                and binding.get('resolved_snapshot_id') == metadata.get('snapshot_id')
                and binding.get('source_sha256') == metadata.get('source_sha256')
                and binding.get('source_sha256')):
            return binding
    except (OSError, ValueError, AttributeError):
        pass
    return None


def verify_source_rows(directory: Path, binding: dict, fields: dict, records: list) -> bool:
    """Require every saved source field and original narrative to match exactly."""
    source = directory / '01_dpd_closed_deviations.csv'
    try:
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != binding.get('analysis_source_sha256'):
                return False
        requested = {record['id']: record for record in records}
        if len(requested) != len(records) or binding.get('record_count') != len(records):
            return False
        seen = set()
        csv.field_size_limit(2**31 - 1)
        with source.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if (columns != binding.get('fields') or len(set(columns)) != len(columns)
                    or not REQUIRED.issubset(columns)):
                return False
            for row in reader:
                identifier = row['pr_id']
                if identifier in seen or identifier not in requested or identifier not in fields:
                    return False
                seen.add(identifier)
                original = fields[identifier]
                for name, value in row.items():
                    key = 'record_name' if name == 'description' and name not in original else name
                    if value != original.get(key, ''):
                        return False
                narrative = requested[identifier].get('source', {})
                if any(narrative.get(name) != original.get(name, '') for name in NARRATIVES):
                    return False
        return seen == set(requested)
    except (OSError, ValueError, csv.Error, KeyError, TypeError, AttributeError):
        return False

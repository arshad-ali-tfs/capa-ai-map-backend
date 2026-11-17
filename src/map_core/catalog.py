"""Non-generative coverage checks for the catalogue shipped with a model."""
import csv
from decimal import Decimal, InvalidOperation
import hashlib
from io import StringIO


def reference_coverage(reference, cluster_ids):
    expected = set(map(int, cluster_ids))
    report = {'status': 'unavailable', 'model_clusters': len(expected), 'reference_clusters': 0,
              'labelled_model_clusters': 0, 'missing_cluster_ids': sorted(expected),
              'unexpected_cluster_ids': [], 'conflicting_cluster_ids': [],
              'invalid_reference_rows': 0, 'reference_sha256': None}
    if not reference.is_file():
        return report
    profiles = {}
    try:
        content = reference.read_bytes()
        report['reference_sha256'] = hashlib.sha256(content).hexdigest()
        reader = csv.DictReader(StringIO(content.decode('utf-8-sig')))
        if not {'final_cluster_id', 'cluster_summary', 'detailed_cluster_summary'}.issubset(reader.fieldnames or []):
            return {**report, 'status': 'invalid'}
        for row in reader:
            try:
                identifier = Decimal(row.get('final_cluster_id') or '')
                if not identifier.is_finite() or identifier < 0 or identifier != identifier.to_integral_value():
                    raise ValueError('Invalid cluster ID')
                identifier = int(identifier)
            except (InvalidOperation, ValueError):
                report['invalid_reference_rows'] += 1
                continue
            profiles.setdefault(identifier, set()).add(((row.get('cluster_summary') or '').strip(),
                                                       (row.get('detailed_cluster_summary') or '').strip()))
    except (OSError, UnicodeError, csv.Error):
        return {**report, 'status': 'invalid'}
    complete = {key for key, values in profiles.items() if len(values) == 1 and all(next(iter(values)))}
    missing = expected - complete
    conflicts = sorted(key for key, values in profiles.items() if len(values) > 1)
    return {**report, 'status': 'invalid' if conflicts or report['invalid_reference_rows'] else 'incomplete' if missing else 'complete',
            'reference_clusters': len(profiles), 'labelled_model_clusters': len(expected & complete),
            'missing_cluster_ids': sorted(missing), 'unexpected_cluster_ids': sorted(set(profiles) - expected),
            'conflicting_cluster_ids': conflicts}

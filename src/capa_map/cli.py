"""Local API launcher and CSV/XLSX source import, owned entirely by MAP."""
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[2]


def configure():
    load_dotenv(ROOT / '.env')
    defaults = {'CAPA_DATA_DIR': './data', 'MAP_ASSETS_DIR': './data/assets', 'HF_HOME': './data/models/huggingface'}
    for key, default in defaults.items():
        os.environ.setdefault(key, default)
    for key in [*defaults, 'CAPA_SOURCE_SNAPSHOT', 'CAPA_SOURCE_CSV', 'CAPA_MAP_LEGACY_RUNS_DIR']:
        value = os.getenv(key)
        if value and not value.startswith(('dbfs:', '/Volumes/')):
            candidate = Path(value)
            os.environ[key] = str((ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve())
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')


def serve():
    configure()
    import uvicorn
    uvicorn.run('capa_map.main:app', host=os.getenv('MAP_HOST', '127.0.0.1'), port=int(os.getenv('MAP_PORT', os.getenv('DATABRICKS_APP_PORT', '8101'))))


def import_source(argv=None):
    configure()
    from capa_map.infrastructure.data.importer import import_snapshot
    from capa_map.infrastructure.data.repository import SqliteRecordRepository
    from capa_map.infrastructure.data.export import export_csv
    parser = argparse.ArgumentParser(description='Import a MAP source CSV/XLSX without model calls.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--sheet')
    args = parser.parse_args(argv)
    data = Path(os.environ['CAPA_DATA_DIR']) / 'sources'
    snapshot = import_snapshot(args.source, data, sheet=args.sheet)
    metadata = SqliteRecordRepository(snapshot).metadata()
    source_csv = snapshot.with_suffix('.csv')
    if not source_csv.exists():
        export_csv(SqliteRecordRepository(snapshot), source_csv)
    for key, value in {'CAPA_SOURCE_SNAPSHOT': str(snapshot), 'CAPA_SOURCE_CSV': str(source_csv), 'MAP_DASHBOARD_RUN_ID': ''}.items():
        set_key(str(ROOT / '.env'), key, value)
    print(json.dumps({'snapshot_id': metadata['snapshot_id'], 'rows': metadata['source_rows'], 'accepted': metadata['accepted_rows'], 'held': metadata['quarantined_rows']}))


if __name__ == '__main__':
    serve()

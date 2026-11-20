import sys
from types import SimpleNamespace

import pytest

from capa_map.infrastructure.web import create_service
from capa_map.worker import _source_configuration


def test_databricks_worker_prefers_table_over_flat_file_settings():
    config = {
        'CAPA_ENV': 'databricks',
        'CAPA_SOURCE_TABLE': 'psg_dev_gxp.gold_capaai.capaai_gtw_complete',
        'CAPA_SOURCE_CSV': './data/sources/trackwise_records.csv',
        'CAPA_SOURCE_SNAPSHOT': './data/sources/source.sqlite3',
    }

    assert _source_configuration(config) == (
        'table',
        'psg_dev_gxp.gold_capaai.capaai_gtw_complete',
        '',
    )


def test_databricks_worker_requires_table_instead_of_falling_back_to_csv():
    config = {
        'CAPA_ENV': 'databricks',
        'CAPA_SOURCE_CSV': './data/sources/trackwise_records.csv',
        'CAPA_SOURCE_SNAPSHOT': './data/sources/source.sqlite3',
    }

    with pytest.raises(ValueError, match='flat-file sources are disabled'):
        _source_configuration(config)


def test_local_worker_keeps_csv_source_behavior():
    config = {
        'CAPA_ENV': 'local',
        'CAPA_SOURCE_TABLE': 'psg_dev_gxp.gold_capaai.capaai_gtw_complete',
        'CAPA_SOURCE_CSV': './data/sources/trackwise_records.csv',
        'CAPA_SOURCE_SNAPSHOT': './data/sources/source.sqlite3',
    }

    assert _source_configuration(config) == (
        'csv',
        './data/sources/trackwise_records.csv',
        './data/sources/source.sqlite3',
    )


def test_databricks_api_uses_table_resource_even_with_snapshot_configured(monkeypatch):
    monkeypatch.setenv('CAPA_ENV', 'databricks')
    monkeypatch.setenv('DATABRICKS_APP_NAME', 'map-app')
    monkeypatch.setenv('CAPA_SOURCE_TABLE', 'psg_dev_gxp.gold_capaai.capaai_gtw_complete')
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT', './data/sources/stale.sqlite3')
    created = []

    class FakeDatabricksRepository:
        def __init__(self, table):
            created.append(table)

    monkeypatch.setitem(
        sys.modules,
        'capa_map.infrastructure.databricks_repo',
        SimpleNamespace(DatabricksRecordRepository=FakeDatabricksRepository),
    )

    create_service()

    assert created == ['psg_dev_gxp.gold_capaai.capaai_gtw_complete']


def test_databricks_api_requires_table_resource_even_with_snapshot(monkeypatch):
    monkeypatch.setenv('CAPA_ENV', 'databricks')
    monkeypatch.setenv('DATABRICKS_APP_NAME', 'map-app')
    monkeypatch.delenv('CAPA_SOURCE_TABLE', raising=False)
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT', './data/sources/source.sqlite3')

    with pytest.raises(RuntimeError, match='flat-file sources are disabled'):
        create_service()

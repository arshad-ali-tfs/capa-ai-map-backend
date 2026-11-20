"""Migration contracts across retained SQLite and warehouse-export sources."""
import csv
import hashlib
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock
import json

import pytest

from capa_map.application import dashboard, runs


@pytest.fixture
def warehouse_run(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    monkeypatch.delenv('CAPA_SOURCE_SNAPSHOT',raising=False)
    source=tmp_path/'sources'/'warehouse.csv';source.parent.mkdir()
    with source.open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=['PR_ID','RECORD_TYPE','PARENT_ID','ROOT_PARENT_ID','STATUS','Criticality','DATE_OPENED','DATE_CLOSED','DETAILED_DESCRIPTION'])
        writer.writeheader()
        writer.writerows([
            {'PR_ID':'001','RECORD_TYPE':'Deviation','Criticality':'Major','DATE_OPENED':'2026-01-01','DATE_CLOSED':'2026-01-04','DETAILED_DESCRIPTION':'Original limit <0.10% and result >0.20%.'},
            {'PR_ID':'002','RECORD_TYPE':'Deviation','Criticality':'Minor'},
            {'PR_ID':'CAPA-A','RECORD_TYPE':'CAPA','PARENT_ID':'001.0','ROOT_PARENT_ID':'002.000','STATUS':'Closed - Done'},
            {'PR_ID':'CAPA-B','RECORD_TYPE':'CAPA','PARENT_ID':'002','STATUS':'Implementation'},
        ])
    snapshot=hashlib.sha256(source.read_bytes()).hexdigest()
    payload={'run_id':'a'*32,'status':'completed','snapshot_id':snapshot,
             'metadata':{'source_snapshot_id':snapshot,'source_csv_path':str(source),'source_table':'catalog.schema.records'},
             'records':[{'id':'001','finalCluster':7,'source':{'detailed_description':'Original limit <0.10% and result >0.20%.'}}, {'id':'002','finalCluster':None,'source':{}}],
             'clusters':[{'id':7,'historicalTitle':'Fictional group','historicalSummary':'Fictional context'}]}
    (runs.root()/payload['run_id']).mkdir()
    return source,payload


def test_warehouse_snapshot_restores_kpis_and_unassigned_capa_links_without_live_query(warehouse_run):
    _,payload=warehouse_run;before=deepcopy(payload)
    with patch.object(runs,'results',return_value=payload):
        data=dashboard.dataset(payload['run_id'])
    assert data['analysis']['sourceAvailable'] and data['analysis']['sourceRecordCount']==4
    assert data['analysis']['sourceResolution']=='original_snapshot'
    assert data['records'][0]['criticality']=='Major' and data['records'][0]['daysOpen']==3
    assert data['records'][1]['clusterId']==''
    assert {capa['id']:capa['recordIds'] for capa in data['capas']}=={'CAPA-A':['001','002'],'CAPA-B':['002']}
    assert [capa['id'] for capa in data['clusters'][0]['capas']]==['CAPA-A']
    assert payload==before


def test_edited_export_cannot_masquerade_as_the_saved_source(warehouse_run):
    source,payload=warehouse_run
    source.write_bytes(source.read_bytes().replace(b'Major',b'Minor'))
    assert not dashboard.source_context(payload)['available']


def test_source_path_outside_run_storage_is_not_accepted(warehouse_run,monkeypatch,tmp_path):
    _,payload=warehouse_run
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path/'different-root'))
    assert not dashboard.source_context(payload)['available']


def test_missing_unrelated_sqlite_does_not_hide_valid_retained_warehouse_export(warehouse_run,monkeypatch,tmp_path):
    _,payload=warehouse_run
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT',str(tmp_path/'missing.sqlite3'))
    assert dashboard.source_context(payload)['available']


def test_csv_projection_preserves_source_population_for_quality_coverage(warehouse_run):
    _,payload=warehouse_run
    context=dashboard.source_context(payload)
    assert context['population']=={'Deviation':2,'CAPA':2}
    assert context['fields']['001']['detailed_description']=='Original limit <0.10% and result >0.20%.'


def test_sql_connection_uses_hostname_and_lazy_apps_credentials(monkeypatch):
    from capa_map.infrastructure import databricks_repo as driver
    auth=lambda:{'Authorization':'test-only'}
    monkeypatch.setattr(driver,'Config',lambda:SimpleNamespace(host='https://example.cloud.databricks.com/',authenticate=auth))
    monkeypatch.setenv('DATABRICKS_WAREHOUSE_ID','warehouse-test')
    with patch.object(driver.sql,'connect',return_value='connection') as connect:
        assert driver._connect()=='connection'
    assert connect.call_args.kwargs['server_hostname']=='example.cloud.databricks.com'
    assert connect.call_args.kwargs['credentials_provider']() is auth


def test_sql_export_preserves_dates_scientific_text_and_leading_zero_ids(monkeypatch,tmp_path):
    from capa_map.infrastructure import databricks_repo as driver
    columns=['PR_ID','PSG_DIVISION','RECORD_TYPE','STATUS','DETAILED_DESCRIPTION','INVESTIGATION','CONCLUSION','ROOT_PARENT_RECORD_TYPE','DATE_OPENED']
    batches=[[('001','DPD','Deviation','Closed - Done','Limit <0.10%; result >0.20%.','','','Deviation',date(2026,1,1))],[]]
    class Cursor:
        description=[(name,) for name in columns]
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement):assert 'ORDER BY PR_ID' in statement
        def fetchmany(self,size):return batches.pop(0)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def cursor(self):return Cursor()
    monkeypatch.setattr(driver,'_connect',lambda:Connection())
    target=tmp_path/'warehouse.csv'
    driver.DatabricksRecordRepository('catalog.schema.records').export_csv(target)
    with target.open(encoding='utf-8-sig',newline='') as stream:row=next(csv.DictReader(stream))
    assert row['PR_ID']=='001' and row['DATE_OPENED']=='2026-01-01'
    assert row['DETAILED_DESCRIPTION']=='Limit <0.10%; result >0.20%.'


def test_worker_runs_warehouse_source_and_retains_a_readable_snapshot(monkeypatch,tmp_path):
    import numpy as np
    import pandas as pd
    import map_pipeline
    from capa_map.infrastructure import databricks_repo as driver
    from capa_map.infrastructure.synthetic import SYNTHETIC_RECORD
    from capa_map.worker import run
    assets=tmp_path/'assets';assets.mkdir()
    directory=tmp_path/'map-runs'/('d'*32);directory.mkdir(parents=True)
    request={'run_id':directory.name,'source_mode':'historical','limit':0,'pr_ids':[],'summary_mode':'pending'}
    (directory/'request.json').write_text(json.dumps(request),encoding='utf-8')
    monkeypatch.setenv('CAPA_ENV','databricks')
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('CAPA_SOURCE_TABLE','catalog.schema.records')
    monkeypatch.setenv('MAP_ASSETS_DIR',str(assets))
    class Warehouse:
        def __init__(self,table):assert table=='catalog.schema.records'
        def export_csv(self,target,**kwargs):
            pd.DataFrame([{**SYNTHETIC_RECORD,'PR_ID':'001'},
                          {**SYNTHETIC_RECORD,'PR_ID':'CAPA-1','RECORD_TYPE':'CAPA','PARENT_ID':'001.0'}]).to_csv(target,index=False)
    monkeypatch.setattr(driver,'DatabricksRecordRepository',Warehouse)
    model=Mock(model='fictional-offline-model');model.metadata.return_value={'model':model.model}
    monkeypatch.setattr('map_core.llm.LiveLLM',Mock(return_value=model))
    monkeypatch.setattr(map_pipeline,'prepare_stage',lambda frame,*_:frame)
    monkeypatch.setattr(map_pipeline,'extract_stage',lambda frame,*_:frame.assign(**{key:'Fictional '+key for key in runs.FIVEWH}))
    def embed(frame,*args,**kwargs):
        return pd.concat([frame.reset_index(drop=True),pd.DataFrame(np.ones((len(frame),1024)),columns=[f'embedding_{i}' for i in range(1024)])],axis=1)
    monkeypatch.setattr(map_pipeline,'embedding_stage',embed)
    monkeypatch.setattr(map_pipeline,'cluster_stage',lambda frame,*args,**kwargs:frame.assign(cluster_id=7,final_cluster_id=7,second_cluster_id=8,cluster_similarity=.9,cluster_top2_margin=.1,close_assignment_review=False,inference_path='offline_test_transform'))
    run(directory)
    state=json.loads((directory/'status.json').read_text(encoding='utf-8'))
    assert state['status']=='completed'
    metadata=json.loads((directory/'run_metadata.json').read_text(encoding='utf-8'))
    assert metadata['source_table']=='catalog.schema.records'
    snapshot=metadata['source_csv_path']
    from pathlib import Path
    assert hashlib.sha256(Path(snapshot).read_bytes()).hexdigest()==metadata['source_snapshot_id']
    data=dashboard.dataset(directory.name)
    assert data['analysis']['sourceAvailable'] and data['capas'][0]['recordIds']==['001']
    model.generate.assert_not_called()

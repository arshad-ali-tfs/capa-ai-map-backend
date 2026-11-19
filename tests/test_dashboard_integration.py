"""MAP UI contracts: immutable sources, relationships and durable annotations."""
import csv
import importlib
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from capa_map.application import annotations, dashboard, runs
from capa_map.infrastructure.data.importer import import_snapshot
from capa_map.infrastructure.data.repository import SqliteRecordRepository
from capa_map.main import app


@pytest.fixture
def analysis(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    run_id = 'c' * 32
    directory = runs.root() / run_id
    directory.mkdir()
    runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'completed', 'source_mode': 'historical'})
    with (directory / runs.ARTIFACTS['draft'][0]).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['pr_id','final_cluster_id','cluster_similarity','detailed_description','close_assignment_review'])
        writer.writeheader()
        writer.writerows([{'pr_id':'001','final_cluster_id':7,'cluster_similarity':.91,'detailed_description':'FICTIONAL: Lot TEST-1; 20 mL on 12-May-2025.','close_assignment_review':True}, {'pr_id':'002','final_cluster_id':'','cluster_similarity':''}])
    return run_id, directory


def source(tmp_path):
    csv_path = tmp_path / 'fictional.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as handle:
        writer=csv.DictWriter(handle, fieldnames=['PR_ID','RECORD_TYPE','PARENT_ID','ROOT_PARENT_ID','RECORD_NAME','STATUS','DATE_OPENED','DATE_CREATED','DATE_CLOSED','SITE'])
        writer.writeheader()
        writer.writerows([{ 'SITE':'Fictional site', **row } for row in [
            {'PR_ID':'001','RECORD_TYPE':'Deviation','RECORD_NAME':'Fictional deviation','STATUS':'Closed','DATE_OPENED':'2025-05-12','DATE_CREATED':'2025-05-10','DATE_CLOSED':'2025-05-15','SITE':'Fictional site'},
            {'PR_ID':'002','RECORD_TYPE':'Deviation'},
            {'PR_ID':'CAPA-1','RECORD_TYPE':'CAPA','PARENT_ID':'001','ROOT_PARENT_ID':'001','RECORD_NAME':'Fictional linked action','STATUS':'Closed'},
            {'PR_ID':'CAPA-2','RECORD_TYPE':'CAPA','PARENT_ID':'OTHER','ROOT_PARENT_ID':'001','RECORD_NAME':'Fictional root link'},
            {'PR_ID':'CAPA-OTHER','RECORD_TYPE':'CAPA','PARENT_ID':'OTHER','ROOT_PARENT_ID':'OTHER'},
        ]])
    return import_snapshot(csv_path, tmp_path / 'sources')


def test_exact_source_dates_relationships_and_unassigned_accounting(analysis,tmp_path,monkeypatch):
    run_id,directory=analysis
    snapshot=source(tmp_path)
    meta=SqliteRecordRepository(snapshot).metadata()
    state=runs.read_json(directory/'status.json');state['snapshot_id']=meta['snapshot_id'];runs.write_json(directory/'status.json',state)
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT',str(snapshot))
    data=dashboard.dataset(run_id)
    assert len(data['records'])==data['analysis']['recordCount']==2
    assert data['analysis']['sourceRecordCount']==5 and data['analysis']['sourceAvailable']
    first,unassigned=data['records']
    assert first['opened']=='2025-05-12' and first['createdLabel']=='2025-05-10'
    assert first['daysOpen']==3 and first['similarity']==.91 and first['warnings']==[]
    assert unassigned['clusterId']=='' and unassigned['similarity'] is None and unassigned['warnings']
    assert first['sourceNarrative'].endswith('20 mL on 12-May-2025.')
    assert {c['id'] for c in data['clusters'][0]['capas']}=={'CAPA-1','CAPA-2'}
    assert all(c['recordIds']==['001'] for c in data['clusters'][0]['capas'])
    assert data['capas'] == data['clusters'][0]['capas']


def test_decimal_export_relationships_include_unassigned_deviations(analysis, tmp_path, monkeypatch):
    run_id, directory = analysis
    csv_path = tmp_path / 'decimal-links.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['PR_ID', 'SITE', 'RECORD_TYPE', 'PARENT_ID', 'ROOT_PARENT_ID', 'STATUS'])
        writer.writeheader()
        writer.writerows({'SITE': 'Fictional site', **row} for row in [
            {'PR_ID': '001', 'RECORD_TYPE': 'Deviation'},
            {'PR_ID': '002', 'RECORD_TYPE': 'Deviation'},
            {'PR_ID': 'CAPA-1', 'RECORD_TYPE': 'CAPA', 'PARENT_ID': '001.0', 'ROOT_PARENT_ID': '001', 'STATUS': 'Closed - Done'},
            {'PR_ID': 'CAPA-2', 'RECORD_TYPE': 'CAPA', 'PARENT_ID': '002.000', 'ROOT_PARENT_ID': '001.0', 'STATUS': 'Closed - Done'},
            {'PR_ID': 'CAPA-3', 'RECORD_TYPE': 'CAPA', 'PARENT_ID': '002', 'STATUS': 'Closed - Cancelled'},
            {'PR_ID': 'CAPA-OTHER', 'RECORD_TYPE': 'CAPA', 'PARENT_ID': '999.0', 'STATUS': 'Closed - Done'},
        ])
    snapshot = import_snapshot(csv_path, tmp_path / 'decimal-sources')
    repository = SqliteRecordRepository(snapshot)
    state = runs.read_json(directory / 'status.json')
    state['snapshot_id'] = repository.metadata()['snapshot_id']
    runs.write_json(directory / 'status.json', state)
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT', str(snapshot))
    with TestClient(app) as client:
        response = client.get(f'/api/v1/map/runs/{run_id}/dataset')
    assert response.status_code == 200
    data = response.json()
    assert {c['id']: c['recordIds'] for c in data['capas']} == {
        'CAPA-1': ['001'], 'CAPA-2': ['001', '002'], 'CAPA-3': ['002'],
    }
    assert {c['id'] for c in data['clusters'][0]['capas']} == {'CAPA-1', 'CAPA-2'}
    assert data['records'][1]['clusterId'] == ''
    assert repository.get_record('CAPA-1').fields['parent_id'] == '001.0'


@pytest.mark.parametrize('value, ids, expected', [
    ('1608.0', {'1608'}, '1608'),
    ('001.00', {'001', '1'}, '001'),
    ('1608.0', {'1608', '1608.0'}, '1608.0'),
    ('001.0', {'1'}, '001.0'),
    ('1.5', {'1'}, '1.5'),
    ('CAPA-1.0', {'CAPA-1'}, 'CAPA-1.0'),
    ('1e3', {'1000'}, '1e3'),
    (None, {'1'}, ''),
])
def test_parent_resolution_preserves_exact_and_ambiguous_source_identity(value, ids, expected):
    assert dashboard.resolve_parent_id(value, ids) == expected


def test_renamed_and_sdk_cached_snapshot_keep_dimensions(tmp_path,monkeypatch):
    original=source(tmp_path)
    identifier=SqliteRecordRepository(original).metadata()['snapshot_id']
    renamed=original.with_name('download-cache.sqlite3');original.rename(renamed)
    payload={'snapshot_id':identifier,'records':[{'id':'001'}]}
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT',str(renamed))
    assert dashboard.source_context(payload)['fields']['001']['date_opened']=='2025-05-12'
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT','/Volumes/catalog/schema/source/snapshot.sqlite3')
    with patch.object(dashboard,'local_file',return_value=renamed) as resolve:
        result=dashboard.source_context(payload)
        resolve.assert_called_once()
        assert result['available'] and len(result['capas'])==2
    payload['snapshot_id']='f'*64
    with patch.object(dashboard,'local_file',return_value=renamed):
        assert dashboard.source_context(payload)=={'fields':{},'capas':[],'metadata':{},'available':False}


def test_annotations_survive_reimport_and_do_not_change_assignments(analysis):
    run_id,directory=analysis
    original=(directory/runs.ARTIFACTS['draft'][0]).read_bytes()
    with TestClient(app) as client:
        endpoint=f'/api/v1/map/runs/{run_id}/records/001/annotation'
        response=client.put(endpoint,json={'assessment':'Mixed / unclear','note':'FICTIONAL review: 20 mL; TEST-1.','expected_revision':0})
        assert response.status_code==200
        saved=response.json();assert saved['revision']==1 and saved['savedAt']
        assert client.put(endpoint,json={'assessment':'Supports pattern','note':'stale','expected_revision':0}).status_code==409
        assert client.get(f'/api/v1/map/runs/{run_id}/annotations').json()['records']['001']==saved
        assert client.put(f'/api/v1/map/runs/{run_id}/records/UNKNOWN/annotation',json={'assessment':'','note':'','expected_revision':0}).status_code==404
        assert client.put(endpoint,json={'assessment':'approve','note':'','expected_revision':1}).status_code==422
        assert client.put(endpoint,json={'assessment':'','note':'x'*10001,'expected_revision':1}).status_code==422
        assert client.put(endpoint,json={'assessment':'','note':'','expected_revision':-1}).status_code==422
    importlib.reload(annotations)
    assert annotations.read(run_id)['001']==saved
    assert dashboard.dataset(run_id)['records'][0]['annotation']==saved
    with TestClient(app) as client:
        response=client.get(f'/api/v1/map/runs/{run_id}/dataset')
        assert response.status_code==200 and response.json()['records'][0]['annotation']==saved
        assert client.get('/api/v1/map/runs/not-a-job/dataset').status_code==404
    assert (directory/runs.ARTIFACTS['draft'][0]).read_bytes()==original
    assert runs.results(run_id)['records'][0]['assignmentReviewStatus']=='unreviewed'
    assert runs.read_json(directory/'record_annotations.json')['history'][0]['recordId']=='001'


def test_annotations_support_unassigned_records_and_independent_revisions(analysis):
    run_id,_=analysis
    annotations.save(run_id,'001','Supports pattern','First record',0)
    other=annotations.save(run_id,'002','Insufficient information','Unassigned record',0)
    assert other['revision']==1
    updated=annotations.save(run_id,'001','','',1)
    assert updated['revision']==2 and updated['assessment']==updated['note']==''
    assert annotations.read(run_id)['002']==other


def test_annotations_refuse_incomplete_runs_and_unsafe_ids(analysis):
    run_id,directory=analysis
    runs.write_json(directory/'status.json',{'status':'failed'})
    with pytest.raises(runs.RunBusyError):annotations.save(run_id,'001','','',0)
    with TestClient(app) as client:
        assert client.get('/api/v1/map/runs/not-a-run/annotations').status_code==404


def test_fivewh_flows_from_saved_csv_through_results_to_frontend_contract(analysis):
    run_id, directory = analysis
    draft = directory / runs.ARTIFACTS['draft'][0]
    saved = runs.rows(draft)
    facts = dict(zip(runs.FIVEWH, ['Operator', 'TEST-42: 20 mL spill', '12-May-2025', 'EQ-104', 'not_available', 'During transfer']))
    saved[0].update(facts)
    with draft.open('w', newline='', encoding='utf8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(saved[0]))
        writer.writeheader()
        writer.writerows(saved)
    before = draft.read_bytes()
    with TestClient(app) as client:
        result = client.get(f'/api/v1/map/runs/{run_id}/results').json()
        dataset = client.get(f'/api/v1/map/runs/{run_id}/dataset').json()
    assert dataset['records'][0]['fiveWh'] == result['records'][0]['fields'] == facts
    assert dataset['records'][1]['fiveWh'] == dict.fromkeys(runs.FIVEWH, '')
    assert draft.read_bytes() == before

import csv
import json
import logging
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from capa_map.application import dashboard, runs
from capa_map.main import app
from capa_map.infrastructure.data.importer import import_snapshot
from capa_map.infrastructure.data.repository import SqliteRecordRepository
from capa_map.infrastructure.web import create_service


@pytest.fixture
def payload():
    return {'run_id':'b'*32,'status':'completed','created_at':'2026-01-01','records':[
      {'id':'001','finalCluster':7,'site':'Fictional Site','type':'Deviation','similarity':.91,'secondCluster':8,'close':True,'source':{'detailed_description':'Lot AB-123; 20 mL on 12-May-2025.'},'fields':{'what':'Fictional event'}}],
      'clusters':[{'id':7,'historicalTitle':'Historical title','historicalSummary':'Historical context'}]}


def test_dataset_preserves_identity_source_and_does_not_fabricate_missing_values(payload):
    with patch.object(runs,'results',return_value=payload):
        result=dashboard.dataset('b'*32)
    row=result['records'][0]
    assert row['id']=='001' and row['volume']==1 and row['clusterId']=='7'
    assert row['sourceNarrative']=='DETAILED_DESCRIPTION:\nLot AB-123; 20 mL on 12-May-2025.'
    assert row['criticality']==row['status']=='Not provided'
    assert row['daysOpen'] is None and row['monthCounts']=={}
    assert result['clusters'][0]['match']==91
    assert 'capa' not in result['clusters'][0]
    assert set(result)=={'clusters','records','capas','months','refreshedAt','analysis'}
    assert result['capas'] == [] and not result['analysis']['sourceAvailable']
    assert row['similarity']==.91 and row['warnings']==[]
    assert result['analysis']['recordCount']==1


def test_source_snapshot_fields_dates_and_duplicates_are_preserved(tmp_path,monkeypatch,payload):
    source=tmp_path/'source.csv'
    with source.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle)
        writer.writerow(['PR_ID','SITE','RECORD_TYPE','DETAILED_DESCRIPTION','DATE_OPENED','DATE_CLOSED','Criticality','RESPONSIBLE_DEPARTMENT','DATE_TO_CLOSE','STATUS','IS_CLOSED'])
        writer.writerow(['001','Fictional Site','Deviation','AB-123; 20 mL','2026-01-03','2026-01-08','Major','Laboratory','2026-01-08','Closed - Done','1.0'])
        writer.writerow(['001','Fictional Site','Deviation','duplicate','','','','','','',''])
    snapshot=import_snapshot(source,tmp_path/'sources')
    repo=SqliteRecordRepository(snapshot)
    assert repo.metadata()['accepted_rows']==repo.metadata()['quarantined_rows']==1
    assert repo.get_record('001').fields['detailed_description']=='AB-123; 20 mL'
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT',str(snapshot))
    payload['snapshot_id']=repo.metadata()['snapshot_id']
    with patch.object(runs,'results',return_value=payload):result=dashboard.dataset('b'*32)
    row=result['records'][0]
    assert row['opened']=='2026-01-03' and row['daysOpen']==5
    assert row['criticality']=='Major' and row['department']=='Laboratory'
    assert row['dueDate']==row['closedDate']=='2026-01-08' and row['dueStatus']=='closed_on_time'
    assert result['clusters'][0]['parentTheme']
    assert result['analysis']['deadlineAsOf']
    assert result['months']==['2026-01'] and row['monthCounts']=={'2026-01':1}
    payload['snapshot_id']='f'*64
    with patch.object(runs,'results',return_value=payload):different=dashboard.dataset('b'*32)
    assert different['records'][0]['opened']==''
    assert different['records'][0]['criticality']=='Not provided'


def test_source_sections_preserve_exact_fields_without_parsing_body_headings(payload):
    original = {
        'detailed_description': '  Lot AB-123; 20 mL on 12-May-2025.\r\nINVESTIGATION:\nThis heading belongs to the description.  ',
        'investigation': 'Résultat: 未確認\n\n1. TEST-42\n2. 20 µL <literal text>',
        'conclusion': 'Original conclusion.\n',
        'background_information': '',
    }
    payload['records'][0]['source'] = original
    with patch.object(runs, 'results', return_value=payload), TestClient(app) as client:
        response = client.get('/api/v1/map/runs/' + 'b'*32 + '/dataset')
    assert response.status_code == 200
    record = response.json()['records'][0]
    assert record['sourceSections'] == [
        {'key': key, 'label': label, 'text': original[key]}
        for key, label in [('detailed_description', 'Description'), ('investigation', 'Investigation'), ('conclusion', 'Conclusion')]
    ]
    assert record['sourceNarrative'] == '\n\n'.join(key.upper() + ':\n' + value for key, value in original.items() if value)


def test_empty_source_has_no_fabricated_sections(payload):
    payload['records'][0]['source'] = {}
    with patch.object(runs, 'results', return_value=payload):
        record = dashboard.dataset('b'*32)['records'][0]
    assert record['sourceSections'] == [] and record['sourceNarrative'] == ''


def test_empty_completed_dataset_is_valid(payload):
    payload.update(records=[],clusters=[])
    with patch.object(runs,'results',return_value=payload):result=dashboard.dataset('b'*32)
    assert result['records']==result['clusters']==result['months']==[]


def test_chart_label_is_an_excerpt_without_rewriting_historical_content(payload):
    group = payload['clusters'][0]
    title = 'HPLC/UPLC Analytical Systems — System Suitability And Operational Failure Deviations'
    summary = 'Lot AB-123; 20 mL; event on 12-May-2025. Primary Center=HPLC; Key Signals="AB-123"; Secondary="20 mL"; Confidence=High'
    group.update(historicalTitle=title, historicalSummary=summary)
    with patch.object(runs, 'results', return_value=payload):
        cluster = dashboard.dataset('b'*32)['clusters'][0]
    assert cluster['shortTitle'] == 'HPLC/UPLC Analytical Systems'
    assert cluster['title'] == cluster['subtitle'] == title
    assert cluster['context'] == summary
    assert cluster['match'] == 91
    unbroken = 'X' * 100
    assert dashboard.compact_title(unbroken) == 'X' * 63 + '…'


def test_incomplete_dataset_is_not_published(payload):
    payload['status']='running'
    with patch.object(runs,'results',return_value=payload),pytest.raises(runs.RunBusyError):dashboard.dataset('b'*32)


def test_configured_dataset_contract_and_no_additional_services(monkeypatch,payload):
    monkeypatch.setenv('MAP_DASHBOARD_RUN_ID','b'*32)
    with patch.object(runs,'results',return_value=payload),TestClient(app) as client:
        response=client.get('/map/dataset')
        assert response.status_code==200 and response.json()['records'][0]['id']=='001'
        assert client.get('/api/v1/match/health').status_code==404
        assert client.get('/api/v1/author/health').status_code==404
        assert client.post('/api/v1/map/runs/'+('b'*32)+'/annotations',json={}).status_code==405


def test_missing_run_is_visible_and_does_not_return_sample_data(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.delenv('MAP_DASHBOARD_RUN_ID',raising=False)
    with patch.object(runs,'history',return_value=[]),TestClient(app) as client:
        response=client.get('/map/dataset')
        assert response.status_code==503 and 'detail' in response.json()
        assert response.headers.get('x-request-id')
    assert 'map_dataset_unavailable status=503' in caplog.text
    assert 'route=map_dataset status=503' in caplog.text


def test_map_source_api_stays_within_map(tmp_path,monkeypatch):
    source=tmp_path/'source.csv';source.write_text('PR_ID,SITE,RECORD_TYPE\n00042,Fictional Site,Deviation\n',encoding='utf-8')
    repository=SqliteRecordRepository(import_snapshot(source,tmp_path/'sources'))
    service=create_service(repository=repository)
    with TestClient(service) as client:
        assert client.get('/api/v1/map/records/00042').json()['pr_id']=='00042'
        assert client.get('/api/v1/map/records?limit=0').status_code==422
        assert client.get('/api/v1/map/source').json()['accepted_rows']==1
    with pytest.raises(ValueError):create_service('match')


def test_saved_fivewh_values_are_exposed_exactly_without_model_calls(payload, monkeypatch):
    monkeypatch.setenv('MAP_DASHBOARD_RUN_ID', 'b'*32)
    facts = {
        'who': '  Operator TEST-42; team A.\r\nSecond line.  ',
        'what': 'Lot AB-123: 20 mL at 12:45 on 12-May-2025. <literal text>',
        'when': '12-May-2025 at 12:45', 'where': 'Room EQ-104',
        'why': 'not_available', 'how': 'Résultat: 未確認; 0.25 mg.',
    }
    payload['records'][0]['fields'] = facts.copy()
    with patch.object(runs, 'results', return_value=payload), \
         patch('map_core.llm.LiveLLM', side_effect=AssertionError('A read must not create a model client')), TestClient(app) as client:
        for route in ['/map/dataset', '/api/v1/map/runs/' + 'b'*32 + '/dataset']:
            response = client.get(route)
            assert response.status_code == 200
            row = response.json()['records'][0]
            assert row['fiveWh'] == facts
            assert row['sourceSections'][0]['text'] == payload['records'][0]['source']['detailed_description']
            assert row['similarity'] == .91 and row['clusterId'] == '7'
    assert payload['records'][0]['fields'] == facts


@pytest.mark.parametrize('facts', [{}, {'what': 'Saved event', 'when': None}])
def test_missing_saved_fivewh_categories_remain_explicitly_empty(payload, facts):
    payload['records'][0]['fields'] = facts
    with patch.object(runs, 'results', return_value=payload), TestClient(app) as client:
        response = client.get('/api/v1/map/runs/' + 'b'*32 + '/dataset')
    assert response.status_code == 200
    row = response.json()['records'][0]
    assert row['fiveWh'] == {key: facts.get(key) or '' for key in runs.FIVEWH}
    # The source event/metadata must not be used to fill missing extracted values.
    assert row['fiveWh']['who'] == '' and row['fiveWh']['where'] == ''

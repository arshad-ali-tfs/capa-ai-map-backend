"""Date, coverage and full-source correctness of the deviation insight rankings."""
import csv
from datetime import date
import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from capa_map.application import insights, runs
from capa_map.infrastructure.data.importer import import_snapshot
from capa_map.infrastructure.data.repository import SqliteRecordRepository
from capa_map.main import app
from capa_map.schemas import DeviationInsights


def projection(*fields):
    return tuple((str(index), *(row.get(key) for key in insights.DIMENSION_FIELDS)) for index, row in enumerate(fields))


def summary(rows, as_of=date(2026, 11, 21), available=insights.DIMENSION_FIELDS):
    return insights.summarize(projection(*rows), available, len(rows) + 2, as_of=as_of, run_id='d'*32, snapshot_id='a'*64)


def test_rolling_window_uses_opened_dates_and_counts_each_deviation_once():
    data=summary([
        {'date_opened':'2025-11-20','client':'Excluded old'},
        {'date_opened':'2025-11-21','client':' Client A  ','product_name':'P-001'},
        {'date_opened':'2026-11-21T22:00:00','client':'client a','product_name':'P-001'},
        {'date_opened':'2026-11-22','client':'Excluded future'},
        {'date_opened':'invalid','client':'Excluded undated'},
        {'date_opened':'2026-06-01','client':'N/A','is_product_impacted':'Yes'},
    ])
    assert data['dateFrom']=='2025-11-21' and data['dateTo']=='2026-11-21'
    assert data['windowDeviationCount']==3 and data['deviationCount']==6
    assert data['undatedCount']==data['futureDatedCount']==1
    assert data['latestOpened']=='2026-11-21'
    assert data['clients']['rows']==[{'label':'Client A','count':2,'percent':66.7}]
    assert data['clients']['knownCount']==2 and data['clients']['missingCount']==1
    assert data['products']['rows'][0]['label']=='P-001'
    DeviationInsights.model_validate(data)


def test_ties_are_deterministic_missing_values_not_ranked_and_denominator_is_all_period_deviations():
    data=summary([{'date_opened':'2026-01-01','client':name} for name in ['Zulu','Alpha','Echo','Delta','Not provided','']])
    ranks=data['clients']
    assert [row['label'] for row in ranks['rows']]==['Alpha','Delta','Echo']
    assert ranks['cutoffTied'] and ranks['topCount']==3 and ranks['topShare']==50
    assert ranks['knownCount']==4 and ranks['missingCount']==2 and ranks['distinctCount']==4


@pytest.mark.parametrize('fields,expected,mode',[
    ({'stage':'QC','process_step':'Filling'},'QC','recorded'),
    ({'process_step':'Secondary Packaging','responsible_department':'quality assurance'},'Packaging','mapped'),
    ({'process_step':'Analytical Testing','responsible_department':'manufacturing'},'QC','mapped'),
    ({'process_step':'Chromatography','responsible_department':'qc - analytical'},'QC','mapped'),
    ({'process_step':'Chromatography','responsible_department':'downstream manufacturing'},'Manufacturing','mapped'),
    ({'process_step':'Filling'},'Manufacturing','mapped'),
    ({'responsible_department':'manufacturing & packaging operations'},'','unavailable'),
    ({'process_step':'Other','responsible_department':'site'},'','unavailable'),
    ({'responsible_department':'warehouse'},'Warehousing / logistics','mapped'),
    ({'responsible_department':'QC - microbiology'},'QC','mapped'),
])
def test_stage_mapping_uses_explicit_operational_fields_and_avoids_ambiguous_departments(fields,expected,mode):
    assert insights.stage_value(fields)==(expected,mode)


def test_blank_clients_and_absent_products_remain_unavailable_instead_of_inferred():
    data=summary([{'date_opened':'2026-04-01','client':'','is_product_impacted':'Yes','detailed_description':'Client made Product Example','process_step':'Filling'}], available=('client','process_step'))
    assert data['clients']['rows']==[] and data['products']['rows']==[]
    assert 'blank' in data['clients']['unavailableReason']
    assert 'No structured product field' in data['products']['unavailableReason']
    assert data['stages']['rows'][0]['label']=='Manufacturing'
    assert data['stages']['mappedCount']==1


def test_leap_year_and_empty_period_are_explicit():
    data=summary([{'date_opened':'2023-02-28','client':'Boundary'}], as_of=date(2024,2,29))
    assert data['dateFrom']=='2023-02-28' and data['windowDeviationCount']==1
    empty=summary([{'date_opened':'2020-01-01','client':'Old'}])
    assert empty['windowDeviationCount']==0 and empty['clients']['unavailableReason']=='No deviations were opened in this period.'


@pytest.fixture
def source_run(tmp_path,monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    run_id='d'*32;directory=runs.root()/run_id;directory.mkdir(parents=True)
    source=tmp_path/'fictional-insights.csv'
    with source.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['PR_ID','SITE','RECORD_TYPE','DATE_OPENED','CLIENT','PRODUCT_NAME','PROCESS_STEP'])
        writer.writeheader()
        for i,record_type in enumerate(['Deviation','Deviation','Deviation','CAPA']):
            writer.writerow({'PR_ID':str(i),'SITE':'Fictional','RECORD_TYPE':record_type,'DATE_OPENED':'2026-06-01','CLIENT':'Client A' if i!=2 else 'Client B','PRODUCT_NAME':'Product P','PROCESS_STEP':'Secondary Packaging'})
    snapshot=import_snapshot(source,tmp_path/'sources')
    snapshot_id=SqliteRecordRepository(snapshot).metadata()['snapshot_id']
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT',str(snapshot))
    runs.write_json(directory/'status.json',{'run_id':run_id,'status':'completed','source_mode':'historical','snapshot_id':snapshot_id})
    with (directory/runs.ARTIFACTS['draft'][0]).open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['pr_id','final_cluster_id','cluster_similarity'])
        writer.writeheader();writer.writerow({'pr_id':'0','final_cluster_id':'7','cluster_similarity':.9})
    return run_id,directory,snapshot,source


def test_endpoint_ranks_full_source_not_only_the_one_record_analysed(source_run):
    run_id,_,snapshot,_=source_run
    digest=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    response=TestClient(app).get(f'/api/v1/map/runs/{run_id}/insights?as_of=2026-11-21')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['sourceRecordCount']==4 and data['deviationCount']==data['windowDeviationCount']==3
    assert data['clients']['rows'][0]['count']==2 and data['clients']['topShare']==100
    assert data['stages']['rows']==[{'label':'Packaging','count':3,'percent':100.0}]
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==digest
    assert TestClient(app).get(f'/api/v1/map/runs/{run_id}/insights?as_of=2026-02-30').status_code==422


def test_unverified_snapshot_does_not_silently_use_current_source(source_run):
    run_id,directory,_,_=source_run
    status=runs.read_json(directory/'status.json');status['snapshot_id']='f'*64;runs.write_json(directory/'status.json',status)
    data=insights.for_run(run_id,date(2026,11,21))
    assert not data['available'] and data['windowDeviationCount'] is None
    assert data['clients']['rows']==[] and 'verified source snapshot' in data['clients']['unavailableReason']
    DeviationInsights.model_validate(data)


def test_verified_warehouse_csv_is_supported_without_a_sqlite_snapshot(source_run,monkeypatch):
    run_id,directory,_,source=source_run
    identity=hashlib.sha256(source.read_bytes()).hexdigest()
    state=runs.read_json(directory/'status.json');state['snapshot_id']=identity
    state['metadata']={'source_snapshot_id':identity,'source_csv_path':str(source)}
    runs.write_json(directory/'status.json',state)
    runs.write_json(directory/runs.ARTIFACTS['metadata'][0],state['metadata'])
    monkeypatch.delenv('CAPA_SOURCE_SNAPSHOT')
    data=insights.for_run(run_id,date(2026,11,21))
    assert data['available'] and data['windowDeviationCount']==3 and data['snapshotId']==identity
    assert data['products']['rows'][0]['label']=='Product P'

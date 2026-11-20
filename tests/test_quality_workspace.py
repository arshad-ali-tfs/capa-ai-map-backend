import csv
from copy import deepcopy
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from capa_map.main import app
from capa_map.application import runs,quality,curation
from capa_map.application.selection import select_records

RUN='c'*32

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    directory=runs.root()/RUN;directory.mkdir()
    runs.write_json(directory/'request.json',{'run_id':RUN,'source_mode':'synthetic','created_at':'2026-01-01'})
    runs.write_json(directory/'status.json',{'run_id':RUN,'status':'completed'})
    rows=[{'pr_id':'001','final_cluster_id':'7','cluster_summary':'Analytical testing — laboratory procedures',
           'proposed_cluster_summary':'Product — reported lack of efficacy','proposed_detailed_cluster_summary':'Fictional reported lack of efficacy; cause unknown.',
           'proposal_scope':'current_batch_only','source_detailed_description':'Fictional equipment EQ-001 in room W123; limit <0.10%.',
           'what':'Fictional complaint'},
          {'pr_id':'002','final_cluster_id':'8','cluster_summary':'Packaging and labelling','what':'Fictional label event'}]
    fields=sorted(set().union(*(row.keys() for row in rows)))
    target=directory/'07_map_dpd_final_draft.csv'
    with target.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    with TestClient(app) as value:
        yield value,target

def change(**values):
    return {'operation':'cluster','expected_revision':0,'cluster_id':'7','title':'Reviewed fictional complaints',
            'theme':curation.THEMES[10],'status':'reviewed','reviewer':'Fictional quality reviewer',
            'rationale':'Member evidence describes fictional product complaints.',**values}

def test_current_theme_conflict_is_flagged_even_when_assignment_score_is_high(client):
    api,_=client
    data=api.get(f'/api/v1/map/runs/{RUN}/dataset').json()
    assert data['clusters'][0]['title']=='Product — reported lack of efficacy'
    assert data['clusters'][0]['historicalContext']['title'].startswith('Analytical testing')
    assert any('different quality themes' in text for text in data['records'][0]['warnings'])
    assert data['records'][0]['sourceMentions'][0]['value']=='EQ-001'

def test_review_title_and_membership_overlay_never_rewrite_original_analysis(client):
    api,target=client;original=target.read_bytes()
    route=f'/api/v1/map/runs/{RUN}'
    assert api.put(route+'/curation',json=change()).status_code==200
    stale=api.put(route+'/curation',json=change())
    assert stale.status_code==409
    data=api.get(route+'/dataset').json()
    assert data['clusters'][0]['titleSource']=='reviewed' and data['review']['revision']==1
    moved=api.put(route+'/curation',json=change(operation='assignment',expected_revision=1,record_id='001',cluster_id='8'))
    assert moved.status_code==200
    data=api.get(route+'/dataset').json();record=next(row for row in data['records'] if row['id']=='001')
    assert record['clusterId']=='8' and record['originalClusterId']=='7'
    assert next(group for group in data['clusters'] if group['id']=='8')['membershipChanged']
    assert target.read_bytes()==original
    assert len(data['review']['history'])==2
    restored=api.put(route+'/curation',json=change(operation='reset_assignment',expected_revision=2,record_id='001'))
    assert restored.status_code==200
    assert api.get(route+'/dataset').json()['records'][0]['clusterId']=='7'

def test_reviewer_can_split_a_record_into_a_new_group_with_a_traceable_parent_theme(client):
    api,_=client;route=f'/api/v1/map/runs/{RUN}'
    created=api.put(route+'/curation',json=change(cluster_id='review-new',title='Fictional separate mechanism',status='needs_review'))
    assert created.status_code==200
    assert api.put(route+'/curation',json=change(operation='assignment',cluster_id='review-new',record_id='001',expected_revision=1)).status_code==200
    data=api.get(route+'/dataset').json()
    group=next(group for group in data['clusters'] if group['id']=='review-new')
    assert group['parentTheme']==curation.THEMES[10] and group['analysisContext'] is None

@pytest.mark.parametrize('values',[{'record_id':'OUTSIDE','operation':'assignment'}, {'theme':'Invented category'}, {'reviewer':'  '},{'rationale':'          '}])
def test_invalid_reviews_are_rejected_without_a_revision(client,values):
    api,_=client;route=f'/api/v1/map/runs/{RUN}/curation'
    assert api.put(route,json=change(**values)).status_code==422
    assert api.get(route).json()['revision']==0

def test_selection_is_reproducible_and_covers_available_years_without_editing_source():
    frame=pd.DataFrame([{'pr_id':str(year*100+i),'date_opened':f'{year}-01-01','site':f'S{i%3}','criticality':['Minor','Major','Critical'][i%3]} for year in range(2020,2027) for i in range(20)])
    original=frame.copy(deep=True)
    first=select_records(frame,14,'stratified');second=select_records(frame,14,'stratified')
    assert first.pr_id.tolist()==second.pr_id.tolist() and first.pr_id.nunique()==14
    assert len(set(first.date_opened.str[:4]))==7
    assert frame.equals(original)
    assert select_records(frame,10,'source_order').equals(frame.head(10))
    assert select_records(frame,0).equals(frame)

def test_generic_renaming_is_not_falsely_reported_as_a_semantic_misassignment():
    assert not quality.titles_differ('Historical name','Current analysis title')
    assert quality.titles_differ('Laboratory analytical testing','Product complaints and adverse events')

def test_split_is_one_atomic_revision_and_preserves_original_source(client):
    api,target=client;original=target.read_bytes();route=f'/api/v1/map/runs/{RUN}'
    response=api.put(route+'/curation',json=change(operation='split',record_id='001',cluster_id='review-atomic',title='Separate complaint mechanism'))
    assert response.status_code==200
    data=api.get(route+'/dataset').json()
    assert data['review']['revision']==1 and len(data['review']['history'])==1
    assert data['records'][0]['clusterId']=='review-atomic'
    assert next(group for group in data['clusters'] if group['id']=='review-atomic')['reviewedContext']
    assert target.read_bytes()==original

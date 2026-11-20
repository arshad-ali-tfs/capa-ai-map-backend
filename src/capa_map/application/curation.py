"""Versioned reviewer overlays. Original assignments and AI outputs are immutable."""
from filelock import FileLock
from . import runs

from .grouping import THEMES


def empty():
    return {'revision':0,'clusters':{},'assignments':{},'history':[]}


def read(run_id):
    directory=runs.folder(run_id)
    with FileLock(str(directory/'.curation.lock'),timeout=10):
        return runs.read_json(directory/'curation.json',empty())


def save(run_id, request):
    directory=runs.folder(run_id)
    payload=runs.results(run_id)
    if payload.get('status')!='completed':
        raise runs.RunBusyError('Review changes require a completed analysis.')
    original_ids={str(group['id']) for group in payload['clusters'] if group['id'] is not None}
    record_ids={row['id'] for row in payload['records']}
    with FileLock(str(directory/'.curation.lock'),timeout=10):
        state=runs.read_json(directory/'curation.json',empty())
        if request.expected_revision!=state['revision']:
            raise runs.RunBusyError('Review changes were saved elsewhere. Refresh before saving again.')
        values={'reviewer':request.reviewer.strip(),'rationale':request.rationale.strip(),'updatedAt':runs.now()}
        if not values['reviewer'] or len(values['rationale'])<10:
            raise ValueError('Enter your name and a review rationale of at least 10 characters.')
        if request.operation=='split':
            if request.record_id not in record_ids or not request.cluster_id.startswith('review-') or request.cluster_id in original_ids|set(state['clusters']):
                raise ValueError('Choose a record from this analysis and a new reviewer subgroup identifier.')
            if not request.title.strip() or request.theme not in THEMES:
                raise ValueError('A title and an available parent theme are required.')
            state['clusters'][request.cluster_id]={**values,'title':request.title.strip(),'theme':request.theme,'status':'needs_review'}
            state['assignments'][request.record_id]={**values,'clusterId':request.cluster_id}
        elif request.operation=='cluster':
            if request.cluster_id not in original_ids and not request.cluster_id.startswith('review-'):
                raise ValueError('Choose an existing cluster or a new reviewer cluster.')
            if not request.title.strip() or request.theme not in THEMES:
                raise ValueError('A title and an available parent theme are required.')
            state['clusters'][request.cluster_id]={**values,'title':request.title.strip(),'theme':request.theme,'status':request.status}
        elif request.operation=='assignment':
            if request.record_id not in record_ids:
                raise ValueError('The record is outside this analysis.')
            if request.cluster_id not in original_ids|set(state['clusters']):
                raise ValueError('Create or choose the target cluster before moving a record.')
            state['assignments'][request.record_id]={**values,'clusterId':request.cluster_id}
        elif request.operation=='reset_assignment':
            if request.record_id not in record_ids:
                raise ValueError('The record is outside this analysis.')
            state['assignments'].pop(request.record_id,None)
        else:
            if request.cluster_id not in original_ids:
                raise ValueError('Only an original cluster can restore its original label.')
            state['clusters'].pop(request.cluster_id,None)
        state['revision']+=1
        state['history'].append({'revision':state['revision'],'operation':request.operation,'recordId':request.record_id,
                                 'clusterId':request.cluster_id,'title':request.title,'theme':request.theme,'status':request.status,**values})
        runs.write_json(directory/'curation.json',state)
        return state

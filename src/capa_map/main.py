import csv
from io import StringIO
import mimetypes
import logging
import os
from typing import Literal
from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from capa_map.infrastructure.web import create_service
from .application import annotations, runs
from .schemas import Annotation, AnnotationCollection, MapDataset
from filelock import Timeout

app = create_service('map')
logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    client_request_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    limit: int = Field(default=5, ge=0, le=100000)
    source_mode: Literal['historical', 'synthetic'] = 'historical'
    pr_ids: list[str] = Field(default_factory=list, max_length=100000)
    allow_any_root: bool = False
    allow_blank_description: bool = False
    include_dr_types: list[str] = Field(default_factory=list, max_length=100)
    exclude_dr_types: list[str] = Field(default_factory=list, max_length=100)
    stopwords: Literal['spacy', 'none'] = 'spacy'
    batch_size: int = Field(default=32, ge=1, le=256)
    cluster_mode: Literal['auto', 'transform', 'training-only'] = 'auto'
    normalize_input: bool = False
    summary_mode: Literal['live', 'pending', 'reference'] = 'live'
    quality: bool = False

    @model_validator(mode='after')
    def validate_type_policy(self):
        if self.include_dr_types and self.exclude_dr_types:
            raise ValueError('Choose either included or excluded DR types.')
        return self


class ReviewDecision(BaseModel):
    final_cluster_id: int
    review_status: Literal['approved', 'rejected', 'pending']
    reviewer: str = Field(default='', max_length=500)
    review_notes: str = Field(default='', max_length=10000)


class ReviewRequest(BaseModel):
    csv: str | None = Field(default=None, max_length=2000000)
    approvals: list[ReviewDecision] | None = Field(default=None, max_length=100000)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def validate_input(self):
        if (self.csv is None) == (self.approvals is None):
            raise ValueError('Supply either csv or approvals.')
        return self


class ImportRequest(BaseModel):
    key: str | None = None


class AnnotationRequest(BaseModel):
    assessment: Literal['', 'Supports pattern', 'Mixed / unclear', 'Does not support', 'Insufficient information']
    note: str = Field(default='', max_length=10000)
    expected_revision: int = Field(ge=0)


@app.get('/api/v1/map/runs/{run_id}/annotations', response_model=AnnotationCollection)
def record_annotations(run_id: str):
    try:
        return {'records': annotations.read(run_id)}
    except Timeout as error:
        raise HTTPException(409, detail='Another annotation save is in progress. Please retry.') from error
    except (ValueError, FileNotFoundError) as error:
        missing(error)


@app.put('/api/v1/map/runs/{run_id}/records/{record_id}/annotation', response_model=Annotation)
def save_annotation(run_id: str, record_id: str, request: AnnotationRequest):
    try:
        return annotations.save(run_id, record_id, request.assessment, request.note, request.expected_revision)
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except Timeout as error:
        raise HTTPException(409, detail='Another annotation save is in progress. Please retry.') from error
    except (ValueError, FileNotFoundError) as error:
        missing(error)


def missing(error):
    raise HTTPException(404, detail='MAP run or artifact not found.') from error


@app.get('/api/v1/map/options')
def options():
    return {'defaults': RunRequest().model_dump(), 'limits': {'records': 100000, 'zero_limit_means_all': True},
            'choices': {'stopwords': ['spacy', 'none'], 'cluster_mode': ['auto', 'transform', 'training-only'], 'summary_mode': ['live', 'pending', 'reference']},
            'embedding_model': os.getenv('MAP_BGE_MODEL', 'BAAI/bge-large-en-v1.5'),
            'algorithm': 'Sol3fields + BGE + persisted UMAP + FAISS', 'legacy': runs.legacy_catalog(),
            'review_scope': 'cluster_summary_only'}


@app.get('/api/v1/map/runs/legacy-catalog')
def legacy_catalog():
    return runs.legacy_catalog()


@app.post('/api/v1/map/runs/import-legacy')
def import_legacy(request: ImportRequest | None = None):
    try:
        return runs.import_legacy(request.key if request else None)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(409, detail='Retained validation artifacts could not be imported.') from error


@app.post('/api/v1/map/runs', status_code=202)
def start_run(request: RunRequest):
    logger.info(
        'map_run_request_received source_mode=%s limit=%d requested_pr_id_count=%d '
        'include_dr_type_count=%d exclude_dr_type_count=%d',
        request.source_mode, request.limit, len(request.pr_ids),
        len(request.include_dr_types), len(request.exclude_dr_types),
    )
    try:
        values = request.model_dump()
        result = runs.start(**values)
        logger.info('map_run_request_accepted run_id=%s status=%s', result['run_id'], result['status'])
        return result
    except runs.RunBusyError as error:
        logger.warning('map_run_request_rejected reason=worker_already_active')
        raise HTTPException(409, detail=str(error)) from error
    except ValueError as error:
        logger.warning('map_run_request_rejected error_type=%s', type(error).__name__)
        raise HTTPException(422, detail=str(error)) from error


@app.get('/api/v1/map/runs')
def run_history(limit: int = Query(default=30, ge=1, le=100000)):
    """Saved analyses, newest first; raise the limit to select older runs."""
    items = runs.history(limit)
    logger.info(
        'map_run_history_returned limit=%d total=%d completed=%d active=%d failed=%d',
        limit, len(items),
        sum(item.get('status') == 'completed' for item in items),
        sum(item.get('status') in {'queued', 'running'} for item in items),
        sum(item.get('status') == 'failed' for item in items),
    )
    return {'items': items}


@app.get('/api/v1/map/runs/{run_id}')
def run_status(run_id: str):
    try:
        result = runs.status(run_id)
        logger.info(
            'map_run_status run_id=%s status=%s stage=%s error_type=%s',
            run_id, result.get('status'), result.get('stage'), result.get('error_type', ''),
        )
        return result
    except (ValueError, FileNotFoundError) as error:
        missing(error)


@app.post('/api/v1/map/runs/{run_id}/cancel')
def cancel_run(run_id: str):
    try:
        return runs.cancel(run_id)
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except (ValueError, FileNotFoundError) as error:
        missing(error)


@app.get('/api/v1/map/runs/{run_id}/explorer')
@app.get('/api/v1/map/runs/{run_id}/results')
def run_results(run_id: str):
    try:
        return runs.results(run_id)
    except (ValueError, FileNotFoundError) as error:
        missing(error)


@app.post('/api/v1/map/runs/{run_id}/review')
def save_review(run_id: str, request: ReviewRequest):
    try:
        # Validate the job boundary before parsing or loading review inputs.
        runs.folder(run_id)
    except (ValueError, FileNotFoundError) as error:
        missing(error)
    csv_text = request.csv
    if csv_text is None:
        stream = StringIO()
        writer = csv.DictWriter(stream, fieldnames=['final_cluster_id', 'review_status', 'reviewer', 'review_notes'])
        writer.writeheader()
        writer.writerows(item.model_dump() for item in request.approvals)
        csv_text = stream.getvalue()
    try:
        return runs.review(run_id, csv_text, request.expected_revision, merge=request.approvals is not None)
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except (ValueError, csv.Error) as error:
        raise HTTPException(422, detail='Invalid review CSV: ' + str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(409, detail='This run does not retain the assignment and summary inputs required for review.') from error


@app.get('/api/v1/map/runs/{run_id}/download')
def download_results(run_id: str, kind: str = 'draft'):
    try:
        target = runs.download(run_id, kind)
        media = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        return FileResponse(target, media_type=media, filename=target.name)
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except (ValueError, FileNotFoundError) as error:
        missing(error)


@app.get('/api/v1/map/runs/{run_id}/dataset', response_model=MapDataset)
def analysis_dataset(run_id: str):
    """The same frontend contract for an explicitly selected completed run."""
    from .application import dashboard
    try:
        return dashboard.dataset(run_id)
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except Timeout as error:
        raise HTTPException(409, detail='Annotations are being updated. Please retry.') from error
    except (ValueError, FileNotFoundError) as error:
        missing(error)


# The supplied frontend defaults to the configured/latest completed analysis.
@app.get('/map/dataset', response_model=MapDataset)
def frontend_dataset():
    from .application import dashboard
    try:
        result = dashboard.current_dataset()
        logger.info(
            'map_dataset_ready run_id=%s records=%d clusters=%d',
            result['analysis']['runId'], len(result['records']), len(result['clusters']),
        )
        return result
    except runs.RunBusyError as error:
        raise HTTPException(409, detail=str(error)) from error
    except Timeout as error:
        raise HTTPException(409, detail='Annotations are being updated. Please retry.') from error
    except (ValueError, FileNotFoundError) as error:
        logger.warning(
            'map_dataset_unavailable status=503 error_type=%s reason=%s',
            type(error).__name__, str(error),
        )
        raise HTTPException(503, detail=str(error)) from error

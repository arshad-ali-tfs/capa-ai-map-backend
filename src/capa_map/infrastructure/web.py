"""MAP-only HTTP/source boundary. No other service is imported or proxied."""
from dataclasses import asdict
import json
import os
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .data.repository import SqliteRecordRepository


def create_service(name='map', repository=None):
    if name != 'map':
        raise ValueError('This application serves MAP only.')
    if os.getenv('CAPA_ENV', 'local') == 'databricks' and not os.getenv('DATABRICKS_APP_NAME'):
        raise RuntimeError('Databricks mode requires the Databricks Apps identity proxy.')
    if repository is None and os.getenv('CAPA_SOURCE_SNAPSHOT'):
        from .storage import local_file
        repository = SqliteRecordRepository(local_file(os.environ['CAPA_SOURCE_SNAPSHOT']))
    elif repository is None and os.getenv('CAPA_SOURCE_TABLE'):
        from capa_map.infrastructure.databricks_repo import DatabricksRecordRepository
        repository = DatabricksRecordRepository()
    app = FastAPI(title='CAPA.AI MAP API', version='0.1.0')
    origins = json.loads(os.getenv('MAP_CORS_ORIGINS', '["http://127.0.0.1:5173", "http://localhost:5173"]'))
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Content-Type'])

    @app.middleware('http')
    async def request_id(request, call_next):
        response = await call_next(request)
        response.headers['X-Request-ID'] = str(uuid4())
        return response

    def source():
        if repository is None:
            raise HTTPException(503, detail='Import a source file and configure CAPA_SOURCE_SNAPSHOT first.')
        return repository

    @app.get('/api/v1/map/health')
    def health():
        return {'service': 'map', 'status': 'alive', 'mode': os.getenv('CAPA_ENV', 'local')}

    @app.get('/api/v1/map/ready')
    def ready():
        metadata = source().metadata()
        return {'service': 'map', 'source_ready': True, 'snapshot_id': metadata['snapshot_id']}

    @app.get('/api/v1/map/source')
    def source_metadata():
        return source().metadata()

    @app.get('/api/v1/map/source/quarantine')
    def quarantine(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        return {**source().list_quarantine(offset=offset, limit=limit), 'snapshot_id': source().metadata()['snapshot_id']}

    @app.get('/api/v1/map/records')
    def records(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), site: str | None = None, record_type: str | None = None):
        return {**source().list_records(offset=offset, limit=limit, site=site, record_type=record_type), 'snapshot_id': source().metadata()['snapshot_id']}

    @app.get('/api/v1/map/records/{pr_id}')
    def record(pr_id: str):
        item = source().get_record(pr_id)
        if item is None:
            raise HTTPException(404, detail='Record not found.')
        return {**asdict(item), 'snapshot_id': source().metadata()['snapshot_id']}

    return app

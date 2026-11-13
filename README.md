# CAPA.AI MAP backend

Independent FastAPI backend for MAP. The [common React frontend](https://github.com/rahul-jha-tfs/capa-ai-frontend) is a separate repository and calls GET /map/dataset.

## Run locally

Python 3.11, 64-bit is required. This workstation already has its own configured .venv, source snapshot and MAP model assets in this folder.

~~~powershell
.\Start-Map.ps1
~~~

API: http://127.0.0.1:8101 • API documentation: http://127.0.0.1:8101/docs

On a new machine:

~~~powershell
git lfs install
git lfs pull
.\Setup-Map.ps1 -Source ".\data\sources\trackwise_records.csv"
# XLSX is supported; add -Sheet when a workbook has multiple sheets.
~~~

The included source is [data/sources/trackwise_records.csv](data/sources/trackwise_records.csv), stored using Git LFS because it exceeds GitHub's regular file limit. It contains the same 19,643 accepted records used by this local backend. Its contents are unchanged; only its filename is clearer. See the [source notes](data/sources/README.md) for provenance and checksum. Importing it on a new machine creates a new local database snapshot; saved analyses are not included.

Provide the trusted MAP model bundle in data/assets and the BGE cache in data/models/huggingface. The asset bundle contains the persisted UMAP model, FAISS index/centroids, cluster merge/column files, the DPD_PERSISTED_MODEL_MANIFEST.json and the two MAP_DPD_500 reference CSVs. Set the model endpoint and key in local .env. Setup does not download proprietary trained assets. Generated databases, run outputs, model files and credentials are excluded from Git.

## Process

~~~mermaid
flowchart LR
  Source[CSV or XLSX] --> Snapshot[Immutable source snapshot]
  Snapshot --> Filter[Existing MAP eligibility]
  Filter --> Sol[Sol extraction: original three narratives]
  Sol --> BGE[BGE embeddings]
  BGE --> UMAP[Persisted UMAP]
  UMAP --> FAISS[FAISS historical clusters]
  FAISS --> Results[Saved records and summaries]
  Results --> Dataset[GET /map/dataset]
~~~

The existing Sol settings, three-field input, BGE preprocessing and UMAP/FAISS behavior are retained. Source IDs and original text remain available. The mockup dataset joins dimensions against the exact source snapshot recorded by the analysis. Missing source fields and dates remain unavailable. Match values are similarity scores scaled to percentages, not measured clustering accuracy.

Start an analysis through POST /api/v1/map/runs; poll /api/v1/map/runs/{run_id}; inspect /results and /download. The API also retains cancellation, summary review and source browsing. These backend operations do not add controls to the supplied mockup.

GET /map/dataset uses MAP_DASHBOARD_RUN_ID when configured, otherwise the latest completed historical analysis. The local prototype is pinned to the existing forty-record analysis. The source contains 19,643 accepted records; this dashboard displays its selected analysis, not all source records. The supplied reviewer annotation remains a browser-only draft and is not saved by the backend.

## Layout and checks

~~~text
src/capa_map/         API, jobs, dataset projection and source/storage adapters
src/map_core/         Existing extraction, embedding and clustering logic
src/map_pipeline.py  Pipeline stages
src/run_map.py       Persisted-model compatibility helpers
tests/               MAP-only offline tests
data/sources/        Versioned source CSV (Git LFS); local database snapshots ignored
data/assets/         Ignored model assets
data/map-runs/       Ignored analysis output
~~~

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
~~~

The application imports only this project's code and its Python dependencies. A sibling platform folder is not required. See [verification](VERIFICATION.md) and [Databricks deployment preparation](DEPLOYMENT.md).

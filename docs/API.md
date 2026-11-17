# MAP API reference

[Back to setup and architecture](../README.md)

Base URL locally: `http://127.0.0.1:8101`.

This reference covers **all 21 HTTP operations** in the generated OpenAPI schema for the current service entrypoint. Each method/path pair counts once. Request field types, defaults and constraints below come from that schema; purpose, persistence and runtime errors were checked against the handlers. This is a contract audit, not a live model or accuracy test.

Use `/docs` for Swagger **Try it out**, `/redoc` for the alternate schema viewer and `/openapi.json` for the machine-readable contract. FastAPI also serves `/docs/oauth2-redirect` for Swagger authorization callbacks. These framework URLs are not business operations in the totals. Many handlers return dictionaries without a typed OpenAPI response; the response descriptions below explain those bodies. Download handlers return files even where the current schema shows a generic JSON response.

## Reading requests and errors

- Paths below are absolute paths on this service. Braced values are required path parameters; replace them and URL-encode identifiers.
- Unless a multipart body is specified, request bodies use `Content-Type: application/json`. When Body is none, send the request without a body.
- Invalid declared parameters/bodies produce HTTP **422** with FastAPI validation details. Runtime errors below add handler-specific behavior; unexpected infrastructure failures may still return **500**.
- Handler errors normally return `{"detail": "..."}`. HTTP success describes transport/execution, not approval or correctness of generated content.

## Endpoint index

### Service and source

| Method and path | Function |
|---|---|
| [`GET /api/v1/map/health`](#get-api-v1-map-health) | Check that the API process responds. |
| [`GET /api/v1/map/ready`](#get-api-v1-map-ready) | Check that the configured source snapshot is available. |
| [`GET /api/v1/map/source`](#get-api-v1-map-source) | Inspect which source export this instance uses. |
| [`GET /api/v1/map/source/quarantine`](#get-api-v1-map-source-quarantine) | Inspect source rows held aside during import, for example duplicate IDs. |
| [`GET /api/v1/map/records`](#get-api-v1-map-records) | Browse accepted records in the imported source. |
| [`GET /api/v1/map/records/{pr_id}`](#get-api-v1-map-records-pr-id) | Open an accepted source record by its original PR ID. |

### Configure and manage analyses

| Method and path | Function |
|---|---|
| [`GET /api/v1/map/options`](#get-api-v1-map-options) | Discover run defaults, limits and supported choices before building a form. |
| [`GET /api/v1/map/runs/legacy-catalog`](#get-api-v1-map-runs-legacy-catalog) | List retained validation runs that can be imported. |
| [`POST /api/v1/map/runs/import-legacy`](#post-api-v1-map-runs-import-legacy) | Copy retained validation artifacts into this service's run history. |
| [`GET /api/v1/map/runs`](#get-api-v1-map-runs) | List the most recent analyses. |
| [`POST /api/v1/map/runs`](#post-api-v1-map-runs) | Queue a new extraction and clustering analysis. |
| [`GET /api/v1/map/runs/{run_id}`](#get-api-v1-map-runs-run-id) | Poll one analysis and inspect progress or failure details. |
| [`POST /api/v1/map/runs/{run_id}/cancel`](#post-api-v1-map-runs-run-id-cancel) | Stop a queued or running analysis. |

### Read results and downloads

| Method and path | Function |
|---|---|
| [`GET /api/v1/map/runs/{run_id}/results`](#get-api-v1-map-runs-run-id-results) | Inspect analysis records, cluster summaries and diagnostics. |
| [`GET /api/v1/map/runs/{run_id}/explorer`](#get-api-v1-map-runs-run-id-explorer) | Read the same explorer result through its alternate route. |
| [`GET /api/v1/map/runs/{run_id}/dataset`](#get-api-v1-map-runs-run-id-dataset) | Load one completed analysis in the frontend presentation format. |
| [`GET /map/dataset`](#get-map-dataset) | Load the analysis selected for the current MAP dashboard. |
| [`GET /api/v1/map/runs/{run_id}/download`](#get-api-v1-map-runs-run-id-download) | Download a retained artifact selected by kind. |

### Save reviews

| Method and path | Function |
|---|---|
| [`POST /api/v1/map/runs/{run_id}/review`](#post-api-v1-map-runs-run-id-review) | Save cluster-summary review decisions for a completed run. |
| [`GET /api/v1/map/runs/{run_id}/annotations`](#get-api-v1-map-runs-run-id-annotations) | Read saved record-level reviewer assessments and notes. |
| [`PUT /api/v1/map/runs/{run_id}/records/{record_id}/annotation`](#put-api-v1-map-runs-run-id-records-record-id-annotation) | Save one record's assessment/note with revision checking. |

## Endpoint details

<a id="get-api-v1-map-health"></a>

### `GET /api/v1/map/health`

Check that the API process responds.

**Body:** none.

**Returns (200):** Service name, alive status and environment mode.

**Behavior and saved changes:** No model call; does not verify source, model or database readiness.

**Errors:** No route-specific error mapping.

<a id="get-api-v1-map-ready"></a>

### `GET /api/v1/map/ready`

Check that the configured source snapshot is available.

**Body:** none.

**Returns (200):** Source readiness and snapshot identity.

**Behavior and saved changes:** No model call; does not exercise clustering or authoring.

**Errors:** 503 when the source is unavailable.

<a id="get-api-v1-map-source"></a>

### `GET /api/v1/map/source`

Inspect which source export this instance uses.

**Body:** none.

**Returns (200):** Snapshot identity, provenance and accepted/quarantined row accounting.

**Behavior and saved changes:** Reads source metadata.

**Errors:** 503 when no source is configured.

<a id="get-api-v1-map-source-quarantine"></a>

### `GET /api/v1/map/source/quarantine`

Inspect source rows held aside during import, for example duplicate IDs.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `offset` | query | integer | `0` | min 0 |
| `limit` | query | integer | `25` | min 1; max 100 |

**Body:** none.

**Returns (200):** Page containing items, total, offset, limit and snapshot_id; retained source text is included.

**Behavior and saved changes:** This is the import quarantine, separate from any investigation or information-review queue.

**Errors:** 503 when no source is configured.

<a id="get-api-v1-map-records"></a>

### `GET /api/v1/map/records`

Browse accepted records in the imported source.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `offset` | query | integer | `0` | min 0 |
| `limit` | query | integer | `25` | min 1; max 100 |
| `site` | query | string or null | `null` / omitted | - |
| `record_type` | query | string or null | `null` / omitted | - |

**Body:** none.

**Returns (200):** Page containing items, total, offset, limit and snapshot_id.

**Behavior and saved changes:** Optional site and record_type filters use exact values. These records are not limited to analysed MAP records or saved AUTHOR cases.

**Errors:** 503 when no source is configured.

<a id="get-api-v1-map-records-pr-id"></a>

### `GET /api/v1/map/records/{pr_id}`

Open an accepted source record by its original PR ID.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `pr_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** Identity plus normalized fields and original source column/value pairs.

**Behavior and saved changes:** Reads the source; does not create an analysis or case.

**Errors:** 404 when the source record is absent; 503 when no source is configured.

<a id="get-api-v1-map-options"></a>

### `GET /api/v1/map/options`

Discover run defaults, limits and supported choices before building a form.

**Body:** none.

**Returns (200):** defaults, limits, choices, embedding_model, algorithm, legacy catalog and review_scope.

**Behavior and saved changes:** Informational; does not start a run.

**Errors:** No route-specific error mapping.

<a id="get-api-v1-map-runs-legacy-catalog"></a>

### `GET /api/v1/map/runs/legacy-catalog`

List retained validation runs that can be imported.

**Body:** none.

**Returns (200):** Configuration flag and catalog entries with availability and any imported_run_id.

**Behavior and saved changes:** Reads the allowlisted server-side catalog; unconfigured catalogs return no available imports.

**Errors:** No route-specific error mapping.

<a id="post-api-v1-map-runs-import-legacy"></a>

### `POST /api/v1/map/runs/import-legacy`

Copy retained validation artifacts into this service's run history.

**Body:** optional; `application/json`; [ImportRequest](#importrequest).

**Returns (200):** items containing the imported or already-present run statuses.

**Behavior and saved changes:** Optional body/key; omitting key imports all available allowlisted runs. Copies artifacts and provenance; no extraction, embedding or model call. Requires CAPA_MAP_LEGACY_RUNS_DIR.

**Errors:** 422 for an unavailable configuration or unknown key; 409 for an import filesystem failure.

<a id="get-api-v1-map-runs"></a>

### `GET /api/v1/map/runs`

List the most recent analyses.

**Body:** none.

**Query:** `limit` (1–100,000; default 30) controls how many saved runs are returned.

**Returns (200):** items: up to `limit` run statuses, newest first.

**Behavior and saved changes:** The frontend requests the full supported history for its analysis selector and displays completed runs. Status reconciliation can mark an orphaned worker failed.

**Errors:** 422 for an out-of-range limit.

<a id="post-api-v1-map-runs"></a>

### `POST /api/v1/map/runs`

Queue a new extraction and clustering analysis.

**Body:** required; `application/json`; [RunRequest](#runrequest).

**Returns (202):** 202 with a run_id, request and initial status; poll the status route.

**Behavior and saved changes:** Creates a run folder and starts a background worker. Uses the configured model API; summary_mode pending/reference does not disable extraction calls. Only one queued/running run per instance. An optional `client_request_id` makes a retried submission return its existing run, including while that run is active. Reusing the key with different options returns 422. Omit it for ordinary independent requests.

**Errors:** 409 when a run is already active; 422 for invalid options. Later execution failures appear in run status/logs.

<a id="get-api-v1-map-runs-run-id"></a>

### `GET /api/v1/map/runs/{run_id}`

Poll one analysis and inspect progress or failure details.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** Run request and status: queued, running, completed, failed or cancelled.

**Behavior and saved changes:** Reads saved worker state; can mark an orphaned worker failed.

**Errors:** 404 for an invalid or missing run.

<a id="post-api-v1-map-runs-run-id-cancel"></a>

### `POST /api/v1/map/runs/{run_id}/cancel`

Stop a queued or running analysis.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** Updated cancelled run status.

**Behavior and saved changes:** Validates worker identity before stopping its process and saving cancellation; existing run artifacts remain.

**Errors:** 404 for an unknown run; 409 if the run is no longer cancellable.

<a id="get-api-v1-map-runs-run-id-results"></a>

### `GET /api/v1/map/runs/{run_id}/results`

Inspect analysis records, cluster summaries and diagnostics.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** Explorer object with run state, records, clusters, source/5W1H details, reviews and available downloads.

**Behavior and saved changes:** May be read while a run is incomplete; final records are then not yet available. Does not rerun extraction.

**Errors:** 404 for a missing run or required result artifact.

<a id="get-api-v1-map-runs-run-id-explorer"></a>

### `GET /api/v1/map/runs/{run_id}/explorer`

Read the same explorer result through its alternate route.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** Exactly the same handler and contract as /results.

**Behavior and saved changes:** A documented alias, not a second analysis or additional processing stage.

**Errors:** 404 for a missing run or required result artifact.

<a id="get-api-v1-map-runs-run-id-dataset"></a>

### `GET /api/v1/map/runs/{run_id}/dataset`

Load one completed analysis in the frontend presentation format.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** MapDataset: clusters, records, months, refreshedAt and analysis coverage; records include sourceSections and annotations.

**Behavior and saved changes:** Joins results to the analysis source snapshot; does not run models.

**Errors:** 404 for an unknown run; 409 while it is incomplete or an annotation lock times out.

<a id="get-map-dataset"></a>

### `GET /map/dataset`

Load the analysis selected for the current MAP dashboard.

**Body:** none.

**Returns (200):** Same MapDataset contract as the run-specific dataset route.

**Behavior and saved changes:** Uses MAP_DASHBOARD_RUN_ID when configured; otherwise selects the latest completed historical run. No run_id input.

**Errors:** 503 when no usable dashboard run is available; 409 if it is incomplete or an annotation lock times out.

<a id="get-api-v1-map-runs-run-id-download"></a>

### `GET /api/v1/map/runs/{run_id}/download`

Download a retained artifact selected by kind.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |
| `kind` | query | string | `"draft"` | - |

**Body:** none.

**Returns (200):** File attachment, with the actual file's media type/name; not a JSON response.

**Behavior and saved changes:** Only allowlisted kinds are supported. Some artifacts exist only for particular runs. Generating the approvals template may write its CSV. See download kinds below.

**Errors:** 404 for an unknown run/kind or unavailable file; 409 when a requested review artifact needs a completed run.

<a id="post-api-v1-map-runs-run-id-review"></a>

### `POST /api/v1/map/runs/{run_id}/review`

Save cluster-summary review decisions for a completed run.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** required; `application/json`; [ReviewRequest](#reviewrequest).

**Returns (200):** Updated explorer results including review revision/history.

**Behavior and saved changes:** Supply exactly one of approvals or csv. approvals merges decisions; csv replaces the review input. Writes a new review revision and derived review artifacts. This reviews cluster summaries, not record assignments or TrackWise approval.

**Errors:** 404 for an unknown run; 409 for incomplete/busy/stale review state or missing review inputs; 422 for invalid CSV/body.

<a id="get-api-v1-map-runs-run-id-annotations"></a>

### `GET /api/v1/map/runs/{run_id}/annotations`

Read saved record-level reviewer assessments and notes.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |

**Body:** none.

**Returns (200):** AnnotationCollection: records keyed by record ID; empty object when no annotations are saved.

**Behavior and saved changes:** Separate from summary review. Reading requires a valid run, not a completed one.

**Errors:** 404 for an unknown run; 409 for an annotation lock timeout.

<a id="put-api-v1-map-runs-run-id-records-record-id-annotation"></a>

### `PUT /api/v1/map/runs/{run_id}/records/{record_id}/annotation`

Save one record's assessment/note with revision checking.

| Parameter | Location | Type | Required / default | Constraints |
|---|---|---|---|---|
| `run_id` | path | string | **Required** | - |
| `record_id` | path | string | **Required** | - |

**Body:** required; `application/json`; [AnnotationRequest](#annotationrequest).

**Returns (200):** Annotation containing assessment, note, incremented revision and savedAt.

**Behavior and saved changes:** Requires a completed run and a record in it. Writes record_annotations.json and history. expected_revision=0 means no previous annotation. Empty assessment clears that value but retains history. Does not change clustering or QMS data.

**Errors:** 404 for a missing run/record; 409 for stale revision, incomplete run or concurrent save; 422 for invalid body.

## Request bodies

Required/default columns describe omission, not automatic approval or workflow readiness. Array/object factory defaults are shown explicitly where the runtime uses them.

<a id="annotationrequest"></a>

### AnnotationRequest

| Field | Type | Required / default | Constraints |
|---|---|---|---|
| `assessment` | string | **Required** | one of `""`, `"Supports pattern"`, `"Mixed / unclear"`, `"Does not support"`, `"Insufficient information"` |
| `note` | string | `""` | max characters 10000 |
| `expected_revision` | integer | **Required** | min 0 |

<a id="importrequest"></a>

### ImportRequest

| Field | Type | Required / default | Constraints |
|---|---|---|---|
| `key` | string or null | `null` / omitted | - |

<a id="reviewdecision"></a>

### ReviewDecision

| Field | Type | Required / default | Constraints |
|---|---|---|---|
| `final_cluster_id` | integer | **Required** | - |
| `review_status` | string | **Required** | one of `"approved"`, `"rejected"`, `"pending"` |
| `reviewer` | string | `""` | max characters 500 |
| `review_notes` | string | `""` | max characters 10000 |

<a id="reviewrequest"></a>

### ReviewRequest

| Field | Type | Required / default | Constraints |
|---|---|---|---|
| `csv` | string or null | `null` / omitted | max characters 2e+06 |
| `approvals` | array of ReviewDecision or null | `null` / omitted | max items 100000 |
| `expected_revision` | integer or null | `null` / omitted | min 0 |

<a id="runrequest"></a>

### RunRequest

| Field | Type | Required / default | Constraints |
|---|---|---|---|
| `client_request_id` | string or null | `null` / omitted | Exactly 32 lowercase hexadecimal characters; optional idempotency key. |
| `limit` | integer | `5` | min 0; max 100000 |
| `source_mode` | string | `"historical"` | one of `"historical"`, `"synthetic"` |
| `pr_ids` | array of string | `[]` | max items 100000 |
| `allow_any_root` | boolean | `false` | - |
| `allow_blank_description` | boolean | `false` | - |
| `include_dr_types` | array of string | `[]` | max items 100 |
| `exclude_dr_types` | array of string | `[]` | max items 100 |
| `stopwords` | string | `"spacy"` | one of `"spacy"`, `"none"` |
| `batch_size` | integer | `32` | min 1; max 256 |
| `cluster_mode` | string | `"auto"` | one of `"auto"`, `"transform"`, `"training-only"` |
| `normalize_input` | boolean | `false` | - |
| `summary_mode` | string | `"live"` | one of `"live"`, `"pending"`, `"reference"` |
| `quality` | boolean | `false` | - |

## Run options in plain terms

The complete `RunRequest` field table above gives defaults and limits. These are the effects of each option:

| Field | Function |
|---|---|
| `client_request_id` | Optional stable submission key. A network retry with this key reuses the same run instead of starting a duplicate paid analysis. |
| `limit` | Maximum selected records; `0` means all eligible records, subject to the service limit. Default `5`. |
| `source_mode` | `historical` uses the imported source; `synthetic` uses the explicit synthetic fixture. Synthetic mode still uses model extraction. |
| `pr_ids` | Restrict selection to specified source IDs; an empty list imposes no ID restriction. Eligibility rules still apply. |
| `allow_any_root` | Relax the default deviation-root restriction. |
| `allow_blank_description` | Relax the default requirement for a populated Description. |
| `include_dr_types` | Keep the specified deviation types; cannot be combined with `exclude_dr_types`. |
| `exclude_dr_types` | Exclude the specified deviation types; cannot be combined with `include_dr_types`. |
| `stopwords` | Choose the retained `spacy` stopword preparation or `none`. |
| `batch_size` | Embedding batch size; default `32`. |
| `cluster_mode` | Select the retained `auto`, `transform` or `training-only` clustering path. Normal historical assignment uses the persisted assets. |
| `normalize_input` | Opt into the pipeline's input-normalization option; default `false`. |
| `summary_mode` | `live` generates summaries, `pending` leaves them for review, `reference` reuses available reference summaries. Extraction still calls the configured model. |
| `quality` | Request the optional model-assisted cluster-quality sample. This is not an independent clustering accuracy measurement. |

Starting a run returns before it finishes. Poll status, then inspect results/downloads. Source selection or asset/model errors can fail the worker after HTTP 202; inspect its saved error/log. There is no HTTP source-upload or model-training endpoint: source import and asset provisioning are setup/CLI tasks.

## Download kinds

Pass one `kind` query parameter to the download endpoint. Default: `draft`. Availability depends on the run; use its results object's download links to discover retained files. A known kind does not guarantee that file exists.

| `kind` | Retained file / function |
|---|---|
| `draft` | `07_map_dpd_final_draft.csv`: combined draft results |
| `approved` | `07_approved_only.csv`: rows derived from cluster-summary approvals |
| `review` | `06_manual_review_queue.csv`: manual summary-review queue |
| `quality` | `06_cluster_quality_sample.csv`: optional quality sample |
| `report` | `MAP_CONTROLLED_RUN_REPORT.md`: retained controlled-run report |
| `filter` | `01_dpd_closed_deviations.csv`: selected eligible records |
| `filter-report` | `01_filter_report.json`: filtering/accounting details |
| `prepared` | `02_prepared_text.csv`: prepared extraction text |
| `extracted` | `03_extracted_5w1h.csv`: extracted 5W1H |
| `embeddings` | `04_bge_embeddings.csv`: embedding-stage output |
| `clusters` | `05_cluster_assignments.csv`: cluster assignments |
| `cluster-info` | `05_cluster_run_info.json`: clustering configuration/run details |
| `summaries` | `06_cluster_summaries.csv`: cluster titles/summaries |
| `validation` | `validation.json`: retained validation output |
| `metadata` | `run_metadata.json`: analysis metadata |
| `evidence` | `five_field_evidence_review.csv`: retained five-record evidence review |
| `theme-review` | `five_assignment_and_theme_review.csv`: retained assignment/theme review |
| `assignment-review` | `forty_assignment_review_queue.csv`: retained 40-record assignment review |
| `factual-review` | `forty_factual_review_queue.csv`: retained factual-review queue |
| `reference-overlap` | `forty_available_reference_overlap.csv`: retained comparison coverage |
| `controlled-metrics` | `controlled_run_metrics.json`: controlled-run measurements |
| `import-provenance` | `import_provenance.json`: imported-artifact identity/hashes |
| `approvals` | `approvals.csv`: current cluster-summary decisions |
| `approvals-template` | `approvals-template.csv`: editable decisions template |

`review-template` is an additional accepted alias for `approvals-template`. Review-derived downloads follow the current saved review revision. Import catalog keys are `corrected_five_fresh_20261111` and `corrected_forty_fresh_20261111`; they refer to server-side retained validations, not arbitrary uploaded CSVs.

## Client examples

Examples are request shapes, not results from a live test. Replace IDs/revisions with values read from this instance.

Create an analysis with `POST /api/v1/map/runs`:

```json
{"limit": 5, "summary_mode": "live"}
```

Save a record annotation with the annotation PUT route. Use the last read annotation revision, initially zero:

```json
{"assessment": "Supports pattern", "note": "Reviewed the original description and investigation.", "expected_revision": 0}
```

Save summary review decisions with the review POST route. Read the current review revision from results first:

```json
{"approvals": [{"final_cluster_id": 53, "review_status": "approved", "reviewer": "Reviewer", "review_notes": "Title and summary reviewed."}], "expected_revision": 0}
```

On 409, reload current state before deciding whether to resubmit. Record annotations and summary reviews use separate revision histories.

## Frontend response contract

`MapDataset` contains `clusters`, `records`, `months`, `refreshedAt` and `analysis`. `analysis` identifies the run, analysed/source counts, snapshot and selection method. Each record includes source metadata, `clusterId`, `similarity`, warnings, annotation and `sourceSections` entries shaped as `{key, label, text}`. Display these sections separately to preserve source field boundaries. The `fiveWh` object contains the saved extraction strings `who`, `what`, `when`, `where`, `why` and `how` for that same record and run. Empty categories are returned as empty strings; saved unavailable markers remain intact. These are AI extractions to compare with the source, not new source fields. Dataset reads do not generate or modify them and do not call the model. Cluster CAPA relationships come from source links. The full nested schema is available in Swagger under **Schemas** and in [the response models](../src/capa_map/schemas.py).

Close scores between the first and second cluster do not trigger `Needs review`. This applies to saved-run presentation and future pipelines. Future exports keep the legacy `close_assignment_review` column as `False` and leave its former threshold blank; raw similarity, score gap and assignments are preserved. Old run files are not rewritten, so their diagnostic close flags may still appear in raw downloads/results. Missing historical cluster descriptions and unassigned records retain specific warnings. No additional LLM explanation calls are made.

Similarity is a numerical matching signal, not a measured probability of correctness. Neither saving an annotation nor approving a summary changes the original source or establishes an assignment's accuracy.

## Compatibility and inventory scope

Both `/results` and `/explorer` are exposed in Swagger and counted separately because they are separate URLs. They execute the same handler. `/map/dataset` is also deliberately included even though it is outside the `/api/v1/map` prefix. There are no additional hidden domain routers mounted by the current MAP entrypoint.

## Implementation references

- [HTTP handlers and request validators](../src/capa_map/main.py)
- [Source and readiness routes](../src/capa_map/infrastructure/web.py)
- [Run lifecycle, results, downloads and review persistence](../src/capa_map/application/runs.py)
- [Record annotation persistence](../src/capa_map/application/annotations.py)
- [Frontend response models](../src/capa_map/schemas.py)

# Review overlays and reproducible analysis

Dataset responses now separate source Deviation/CAPA totals, eligible deviations, analysed opened-date coverage and selection strategy. Saved analysis timestamps describe processing rather than event recency. Future runs default to deterministic stratification across opened year, site and criticality; `source_order`, `recent`, explicit IDs and all-record requests remain explicit alternatives. This does not change previously saved runs or make a sample representative of operational rates.

Current-analysis cluster descriptions take precedence over historical catalogue labels. Disagreement between recognized quality themes is flagged for member review. Twelve suggested parent themes organize detailed clusters; they are not confirmed root causes. Available assignment margins remain inspection aids without a new arbitrary confidence threshold.

`GET/PUT /api/v1/map/runs/{run_id}/curation` reads/writes a versioned review overlay. Writes require `expected_revision`, reviewer and a rationale. Supported operations review cluster labels/themes, move a record, split into a new reviewer subgroup, restore an original assignment or restore an original cluster label. A split creates and assigns atomically in one revision. Stale revisions receive 409. The original saved analysis and source CSV are never rewritten. AI context is explicitly retained as describing original membership after a move/split.

`GET /api/v1/map/runs/{run_id}/compare?other_run_id=...` compares shared records' original extractions, source hashes and assignments. It excludes reviewer overlays and makes no model calls. Differences between saved runs do not isolate a batch-size effect. Recipe/output fingerprints are exposed; older runs explicitly report missing clustering-asset fingerprints.

Literal equipment/location mentions include source fields and offsets. They do not create verified event metadata. CAPA status and narrative actions remain distinct from demonstrated effectiveness. Source attachments are unavailable unless explicitly supplied in the source system.

Run `python -m pytest -q` for the offline dataset, source recovery, selection, overlay, concurrency and review checks. Use a separate `CAPA_DATA_DIR` for tests and experiments.

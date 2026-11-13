# Local verification — 13 November 2026

- 56 MAP backend tests passed in the backend's own Python 3.11 environment.
- pip check found no broken requirements.
- The distributable Python wheel built successfully and contains the MAP code and prompt templates.
- The MAP core and pipeline syntax trees match the retained implementation.
- Import checks resolve capa_map/map_core from this project; capa_shared, capa_match and capa_author are not installed in its environment.
- A fresh fictional one-record run completed Sol extraction, 1,024-dimensional BGE embedding, persisted UMAP transformation, FAISS assignment and result export. Run ID: f3fe2d863cc64ccbb5a5b425537e298a. All six structural validation checks passed; one pending record and zero approved records. Summary mode was pending. This establishes execution, not measured clustering accuracy.
- The supplied frontend passed 15 real-service browser checks across desktop, tablet and phone.
- The old unified platform processes were stopped before the standalone service/UI tests.

Git eligibility checks exclude .env, generated databases, model assets, run outputs, dependencies, caches and test artifacts. The accepted source export is explicitly included as data/sources/trackwise_records.csv using Git LFS. The source projects are ready to be taken as separate repository roots. No GitHub repository creation/push or Databricks deployment was performed.

## Source inclusion check

The source CSV was renamed to trackwise_records.csv without changing its 121,397,555 bytes or 19,643 accepted records. Its SHA-256 before and after the rename is 475eb66c11a4af2b506aab869cd20b88748243d896e8414affc59387ba74d840. Git attribute checks confirm the LFS filter, diff and merge settings; the CSV is no longer ignored. Credentials and generated database snapshots remain ignored. Backend configuration and setup/deployment notes reference the readable name. After restarting MAP, the backend and frontend proxy returned exactly the same dataset (40 records, 18 clusters). All 56 backend tests passed again. Local reports are .local/source-inclusion.json and .local/source-inclusion-tests.xml. Nothing was staged, committed or pushed.

Local reports and run output are ignored under .local and data. Public-facing documentation contains no credentials or record narratives.

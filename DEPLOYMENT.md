# Databricks Apps deployment preparation

For this service's standalone Docker image and local container startup, see [DOCKER.md](DOCKER.md). The Docker and Databricks Apps deployment paths are verified separately.

These instructions cover the standalone MAP backend and common frontend repositories. MATCH and AUTHOR have their own separate backend repositories and deployment guides.

1. Publish this project folder as its own repository when requested. The named source export data/sources/trackwise_records.csv is included through Git LFS; install Git LFS before adding/committing it and run git lfs pull after cloning. Keep .env, dependencies, caches, generated databases, run outputs and model artifacts out of the repository.
2. Connect the repository source to a Databricks workspace and deploy from this project root. app.yaml contains the start command. Python dependencies are declared in requirements.txt/pyproject.toml; the frontend uses package.json/package-lock.json and builds with npm run build.
3. For MAP, provide the source snapshot/export and trusted UMAP/FAISS/BGE artifacts through approved storage. Configure CAPA_SOURCE_SNAPSHOT, CAPA_SOURCE_CSV, MAP_ASSETS_DIR, HF_HOME and writable CAPA_DATA_DIR. Supply the model endpoint/key through Databricks secret configuration. Install the backend’s databricks extra if SDK-based volume access is required. Model artifacts exceed the app source-file limit and belong in artifact storage, not the app repository.
4. For the common frontend, set MAP_API_TARGET to the MAP app's URL. Configure and validate app-to-app authentication and permission to invoke MAP. The server supports a server-side MAP_API_BEARER_TOKEN; production OAuth token acquisition/renewal still needs environment-specific wiring. No credential is compiled into React.
5. Both launchers bind 0.0.0.0 through app.yaml and use DATABRICKS_APP_PORT in Databricks. Verify source and model accessibility, sufficient compute for BGE/UMAP, persistent run storage and the /map/dataset contract in that environment.
6. Run the backend tests and frontend browser checks again in the deployment environment. Local worker spawning/file locks are suitable for the current single-instance prototype; durable external job execution and shared coordination need to be configured before horizontally scaling it.

Local execution is verified separately; a Databricks deployment has not been performed. This is a deployment starting point, not a claim of cloud validation.

The source CSV belongs to the Git repository through LFS, but should be downloaded and placed in approved data storage when preparing a Databricks deployment. Exclude the large CSV from the app-source upload. Create the runtime database snapshot from that CSV and configure its paths; including the source in Git does not make it part of the Python wheel or bypass app-source size limits. The local forty-record analysis remains local, so a fresh deployment needs a new completed analysis before the dashboard can show results.

References: [Databricks app deployment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy), [dependency configuration](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/dependencies), [runtime environment and ports](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env), [app development and source limits](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-development).

MAP record annotations are persisted with the run artifacts in CAPA_DATA_DIR/map-runs/<run_id>/record_annotations.json. Provision durable run storage and back it up, including annotation history. Per-record revision checks reject stale updates; these annotations do not approve cluster assignments or write to TrackWise. Validate GET dataset/annotations and PUT annotation requests through the deployed frontend proxy as part of acceptance.

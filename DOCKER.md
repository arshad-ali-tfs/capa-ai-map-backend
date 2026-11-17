# MAP Docker image

This repository builds one independent Linux/amd64 image using Python 3.11 and the existing dependency lock. It starts the existing MAP launcher, without changing models, prompts or business workflows. Other backend repositories are not build inputs.

## Recommended: complete setup and pipeline in one command

With Docker Desktop's Linux engine running and access to this private Git repository, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run-Map.ps1
```

This is the supported fresh-machine path. It handles downloads, image build, source import, checksum verification, offline model execution, container startup and the full eligible-record pipeline. No host Python is needed. Enter the model endpoint/key when prompted or configure `.env.docker` first. Use `-Limit 5` for a pilot, `-PrepareOnly` for offline setup, `-ServeOnly` for API startup and `-Stop` to stop the managed container. See [README](README.md#run-locally) for persistence, recovery and alternate ports.

The rest of this document describes lower-level Docker commands for developers who intentionally manage the steps separately.

## Manual preparation

Install Docker Desktop with its Linux engine (or Docker Engine on Linux). From this repository:

~~~powershell
git lfs install
git lfs pull
Copy-Item docker.env.example .env.docker
~~~

Edit `.env.docker` with container paths and service settings. For example, a host file under `data/sources/` appears under `/data/sources/` in the container. Do not copy Windows paths or shell quotes into this file. Keys stay in this ignored local file and are injected at runtime.

Git LFS downloads the source CSV, pinned BGE checkpoint/tokenizer and saved UMAP/FAISS bundle directly from this repository. No separate model-file handover is required. Prepare the source snapshot as described below and configure the model endpoint/key in `.env.docker`. The initial runtime download is approximately 2 GB. The build context excludes source exports, databases, model caches, runs, local environments, Git history and credentials; runtime files are provided through the data mount.

## Advanced foreground launcher after manual preparation

~~~powershell
.\Start-Docker.ps1
~~~

The launcher builds `capa-ai-map:local` when it is missing, creates/reuses the `capa-ai` network, mounts this repo's `data` directory at `/data`, and starts only MAP. It runs as UID/GID 10001, binds the application to `0.0.0.0:8101` inside Docker, and publishes it on `127.0.0.1:8101` on the laptop.

API documentation: http://127.0.0.1:8101/docs. Stop with Ctrl+C. The container is removed on exit; mounted data is retained.

After changing code, rebuild explicitly:

~~~powershell
.\Start-Docker.ps1 -Build
~~~

If the native backend already uses 8101, choose another host port:

~~~powershell
.\Start-Docker.ps1 -Port 18101
~~~

`-DataDirectory`, `-EnvFile` and `-Image` also accept overrides. Keep separate writable data directories for different services. On Linux, grant UID/GID 10001 access to the mounted data directory, or use `docker run --user` with a suitable host UID/GID. A mounted directory's permissions override the image directory's permissions.

## Build and run without the Windows helper

~~~bash
docker build --platform linux/amd64 --tag capa-ai-map:local .
docker network create capa-ai
docker run --rm --init --platform linux/amd64 --name capa-ai-map --network capa-ai --publish 127.0.0.1:8101:8101 --env-file .env.docker --mount "type=bind,source=$(pwd)/data,target=/data" capa-ai-map:local
~~~

Create the network only once; reuse it if it already exists. The Dockerfile also accepts `--build-arg PYTHON_IMAGE=...` to use an approved Python 3.11 Debian Bookworm image, including a digest-pinned image.

## Source and persistent storage

For an existing local installation, reuse its `data` folder and set `CAPA_SOURCE_SNAPSHOT=/data/sources/<actual-snapshot-id>.sqlite3` and `CAPA_SOURCE_CSV=/data/sources/<actual-snapshot-id>.csv`. The three services should reference the same source snapshot identity.

For a fresh folder, build the image first, place the CSV in `data/sources/trackwise_records.csv`, then import it using the existing service command:

~~~powershell
docker build --platform linux/amd64 --tag capa-ai-map:local .
$repoPath = (Resolve-Path .).Path
$dataPath = (Resolve-Path .\data).Path
docker run --rm --mount "type=bind,source=$repoPath,target=/checkout,readonly" capa-ai-map:local python /checkout/src/capa_map/bootstrap.py --root /checkout
docker run --rm --env-file .env.docker --mount "type=bind,source=$dataPath,target=/data" capa-ai-map:local map-import /data/sources/trackwise_records.csv
~~~

The verification command checks all bundled files before models are loaded; stop and fix any failure before running the import. Use `--sheet Sheet1` for an XLSX workbook as needed. The import prints the snapshot ID. Set the two `CAPA_SOURCE_*` paths in the host `.env.docker` to the generated `/data/sources/<id>.sqlite3` and `/data/sources/<id>.csv` files. Import commands write a container-local `/app/.env`; that file is temporary and does not update the host configuration automatically.

The standard data mount already exposes the pulled UMAP/FAISS bundle at `/data/assets` and BGE cache at `/data/models/huggingface`; the provided environment template points there. The spaCy English model and CPU-only Torch are installed during the image build at the existing versions. Validate persisted models on Linux before using them for new analyses; a health probe does not validate model compatibility.

MAP outputs, cluster-summary reviews and record annotations live under `/data/map-runs`. Retain and back up the complete directory. Existing historical results may be reused for display when their source snapshot is available; generating a fresh analysis requires the model assets and configured model endpoint/key. Set `MAP_DASHBOARD_RUN_ID` to a mounted completed run when pinning the UI.

## Verify

~~~powershell
docker inspect --format '{{json .State.Health}}' capa-ai-map
docker exec capa-ai-map python /opt/capa/healthcheck.py --path /api/v1/map/ready
~~~

The built-in Docker health check probes only `/api/v1/map/health`, which proves the API process is alive. The explicit command above checks source availability; also load a completed dataset and run a small analysis to validate BGE/UMAP/FAISS and model connectivity. Missing source data or model assets produce an unavailable response rather than sample results.

This Docker configuration is independent of the Databricks Apps `app.yaml` source-deployment path in [DEPLOYMENT.md](DEPLOYMENT.md). Keep the existing identity, storage and target-environment validation steps for deployment. No Compose file is required.

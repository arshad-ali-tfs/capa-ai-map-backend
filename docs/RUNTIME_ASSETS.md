# MAP runtime files

The repository contains the files needed to run the existing MAP pipeline. There is no separate model archive to request from a teammate.

| Included files | Purpose | Delivery |
|---|---|---|
| `data/sources/trackwise_records.csv` | Shared reference source export; setup creates an indexed local snapshot | Git LFS |
| `data/assets/dpd_umap_model_verified.pkl` | Existing fitted UMAP model | Git LFS |
| Remaining `data/assets/*.pkl` and `*.faiss` | Existing selection columns, cluster merge mapping, centroids and FAISS index | Git LFS |
| Both `data/assets/MAP_DPD_500_*.csv` files | Historical embeddings and cluster-summary references | Git LFS |
| `data/assets/DPD_PERSISTED_MODEL_MANIFEST.json` | Original checksums for the historical clustering artifacts | Git |
| `data/models/huggingface/hub/models--BAAI--bge-large-en-v1.5/` | Pinned BGE weights, tokenizer, Sentence Transformers configuration and original model card | Weights in Git LFS; configuration in Git |
| `requirements-lock.txt` | Exact Python dependencies, including the spaCy English model download | Installed by setup or Docker build |

The BGE revision is `d4aa6901d3a41ba39fb536a557fa166f842b0e09`. Its Hugging Face cache layout and `refs/main` are retained so the unchanged pipeline resolves `BAAI/bge-large-en-v1.5` in offline mode. Unrelated cached models, lock files and temporary cache markers are excluded.

## First setup and verification

Start Docker Desktop's Linux engine and run this from a Git clone of the repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run-Map.ps1
```

The command retrieves missing source/models through Git LFS, builds the image, verifies all files inside Docker, imports the source and tests the real embedding/clustering models before starting live extraction. Python and its dependencies are inside the image; host Python is not required. Use `-PrepareOnly` to stop after offline preparation without making paid API calls. See [the README](../README.md#run-locally) for credentials and lifecycle options.

Saved analyses and reviewer notes are created in the mounted data directory. They are not copied from another user's workspace. A new installation needs a completed pipeline run before the frontend can display results. Existing files that differ from their manifest are rejected rather than silently overwritten.

[runtime-assets.json](../runtime-assets.json) records the relative path, size and SHA-256 of every bundled runtime file. Verification rejects missing files, Git LFS pointer files and changed content before loading any model. Git attributes preserve exact runtime-file bytes on Windows and Linux.

Use a clone with Git LFS rather than an ordinary source ZIP. If a pull fails, check repository access, Git LFS installation and storage/bandwidth availability. See [GitHub's LFS collaboration guide](https://docs.github.com/en/repositories/working-with-files/managing-large-files/collaboration-with-git-large-file-storage).

The bundle does not change extraction prompts, BGE preprocessing, model weights, UMAP/FAISS behavior or historical labels. Check [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for BGE attribution. For Databricks, distribute artifacts from this checkout to approved runtime storage and exclude `data/**` from app-source deployment as described in [DEPLOYMENT.md](../DEPLOYMENT.md).

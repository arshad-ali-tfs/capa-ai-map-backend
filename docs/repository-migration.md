# Databricks repository integration

The migration preserves the new repository's `main` history and Databricks Apps/SQL-table source. Our stable source recovery and complete CAPA relationships are integrated on `codex/map-databricks-integration-20261120`. Original branches retain their existing history as separate checkpoints. No rewrite of the destination main branch is required.

`codex/map-quality-databricks-20261120` adds our review overlays, parent themes, source population, recipe fingerprints and stratified selection to the stable integration. It pairs with the existing frontend `codex/map-match-quality-20261120` branch. The warehouse worker retains its table export and logging alongside quality provenance and selection; saved warehouse and SQLite sources both supply population metadata. Warehouse CSV fields may exceed the CSV reader's default field size, so retained long investigation fields remain readable.

The stable branch supports the approved frontend branch `codex/map-approved-updates-20261120`. Retained content-addressed warehouse CSVs provide the same source fields and top-level CAPA relationships as SQLite snapshots, including unassigned deviations and integer-as-decimal parent links. Warehouse CSV content is checked against its saved SHA-256 before restoring source dimensions. Copied local analyses can still use an explicitly verified equivalent-source binding. Source restoration does not call the model or reassign records.

The Databricks SQL connection resolves Apps credentials when connecting and passes a hostname to the SQL connector. The dependency lock uses `python-dateutil==2.9.0.post0`, compatible with both Pandas 2.2.3 and SQL connector 4.9. The prior lock's dateutil 4 pin could not resolve with the connector's `<3` constraint. Missing optional diagnostic fields do not turn worker/API logging into a failed run.

Reference: [Databricks Apps authentication](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth), [SQL connector configuration](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector).

Offline tests cover the real worker with model stages replaced, SQL export formatting, Apps credential handoff, scientific text and exact record IDs, immutable-source checks, CAPA relationships, source recovery and API contracts. A live warehouse/deployed-app check additionally requires Databricks workspace resources and service-principal permissions; offline checks do not establish that deployment connectivity.

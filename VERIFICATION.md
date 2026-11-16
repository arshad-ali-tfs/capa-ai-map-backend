# Local MAP verification - 16 November 2026

The final backend suite passed 64 offline tests. See `.local/source-reader-tests.xml`. The frontend production build, 60 controlled browser checks and 21 applicable live-source browser checks passed separately; see the common frontend VERIFICATION.md. The 18 controlled boundary checks skipped during the live-source run are not counted as passes.

The dataset now includes `sourceSections` with explicit field keys, display labels and exact source text. HTTP contract tests verify field boundaries, CRLF/whitespace, multilingual content, literal markup, dates, quantities and identifiers. Empty source records return an empty list, and the existing `sourceNarrative` and `sourceField` properties remain compatible. No source-body headings are parsed to guess field boundaries.

The presentation contract now has typed OpenAPI response schemas. Tests cover original source/record identity, accurate opened/created dates, missing values, record-level scores, warnings, unassigned record accounting, explicit parent/root-parent CAPA relationships, and renamed/SDK-cached snapshot resolution without joining a different snapshot.

Record annotations have their own GET/PUT API and versioned storage. Tests cover saved note/revision reload, stale writes, unknown records, invalid assessments, oversized notes, unassigned records, independent per-record revisions and incomplete runs. Annotation saves do not alter assignments, summaries or their approvals. A real HTTP/UI check used clearly fictional data to verify save, reload and persistence after a backend restart. Source import through browser display also verified two fictional CAPA relationships.

The operational dashboard retains the same 40 records and 18 clusters from the 19,643-record source snapshot. Every original narrative and cluster assignment matches the pre-change dataset. Its draft artifact SHA-256 remains 8e900a9398642758970d52171ed91e4844f3e433fb2d47358f281d25559add37. This is an integration and presentation update, not a measured model or clustering-accuracy change.

Chart shortTitle values are now deterministic excerpts of historical titles. Full titles, subtitles, historical context, scores and assignments remain unchanged. A targeted contract test checks that shortening display text preserves source facts, identifiers, dates and quantities.

All model, extraction prompt, three-field input, BGE, UMAP and FAISS code remains unchanged. Reports, fictional verification runs and generated runtime files are ignored. A Databricks deployment has not been performed for this update. Persist CAPA_DATA_DIR, including annotation history, and complete the environment-specific deployment checks in DEPLOYMENT.md before cloud use.

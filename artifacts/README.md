# Artifacts

`artifacts/` is reserved for outputs produced by local or calibration runs.

- `reference/` contains selected historical/reference snapshots that are intentionally version-controlled.
- generated paths such as `question_corpus/`, `calibration/`, `scenario_rag/`, and `runs/` are ignored and may be deleted/rebuilt locally.
- deterministic test fixtures belong under `tests/fixtures/` rather than generated artifact directories.

Do not treat a local artifact as a release baseline unless it is intentionally reviewed and moved into a tracked reference/fixture location.

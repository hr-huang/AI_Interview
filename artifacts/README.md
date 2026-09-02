# Artifacts

`artifacts/` is reserved mainly for outputs produced by local or calibration runs.

- `reference/` contains selected historical/reference snapshots that are intentionally version-controlled.
- generated paths such as `question_corpus/`, `calibration/`, `scenario_rag/`, and `runs/` are ignored and may be deleted/rebuilt locally.
- `question_corpus/task6_source_evidence_fixture.json` is a legacy tracked regression fixture kept at its existing path for test compatibility; new deterministic fixtures belong under `tests/fixtures/`.

Do not treat a local artifact as a release baseline unless it is intentionally reviewed and moved into a tracked reference/fixture location.

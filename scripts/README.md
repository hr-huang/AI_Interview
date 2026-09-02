# Repository operator scripts

This directory contains development, demo, corpus, retrieval-audit, and calibration CLIs. They are repository operations tooling, not part of the installed `profile_agent` runtime package.

Run them from the repository root, for example:

```powershell
uv run python scripts/run_offline_calibration.py --case ALL
uv run python scripts/run_interview_demo.py
uv run python scripts/run_question_bank.py validate
```

`run_scenario_bank.py` intentionally remains at the repository root for now because it is the documented stable Scenario Bank maintenance entrypoint. Moving that public command is deferred until its README/test references can be migrated as one compatibility change.

Generated outputs belong under ignored `artifacts/` runtime paths. Version-controlled reference snapshots belong under `artifacts/reference/` or `tests/fixtures/`.

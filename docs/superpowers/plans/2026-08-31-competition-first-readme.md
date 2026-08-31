# Competition-First README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the developer-log-style README with a competition- and enterprise-first project page whose claims and startup commands are verifiable from the repository.

**Architecture:** Keep all public onboarding in the root project `README.md`. Structure it as an inverted pyramid: product outcome, runnable entry points, enterprise workflow, then technical depth and reproducibility. Do not change runtime code or add deployment infrastructure merely to obtain badges.

**Tech Stack:** Markdown, Mermaid, shields.io badges, Python 3.11+, LangGraph 0.2+, FastAPI, React 19, Vite, Qdrant.

## Global Constraints

- Primary audience is competition judges and enterprise hiring users; developers are secondary.
- Modify the outer GitHub-root `README.md` and the inner project `README.md` only for the public deliverable; do not change runtime, test, environment, dependency, Docker, or license files.
- Use exactly four factual badges: Python 3.11+, LangGraph 0.2+, React 19, and FastAPI 0.116+.
- Do not add License or Docker Pulls badges until a real license and published image exist.
- Quickstart must contain exactly two paths: `uv` fastest and `venv + pip` local development.
- Never include real API keys or invented metrics.
- Distinguish the real assessment route `/assessments/new` from the demo report route `/demo/assessment`.
- State the current role scope as `ai_application_engineering / 2026-H2`.
- Preserve the real calibration facts and disclose the seven Top-3 forbidden diagnostics.
- Disclose that the React candidate interview page is currently an entry shell even though the backend interview runtime and API exist.

---

### Task 1: Rewrite and verify the public README

**Files:**
- Modify: `docs/PROJECT_DETAILS.md` (the original detailed project README)
- Modify: `README.md` at the Git repository root
- Reference: `docs/superpowers/specs/2026-08-31-competition-first-readme-design.md`
- Reference: `pyproject.toml`
- Reference: `.env.example`
- Reference: `web/package.json`
- Reference: `web/src/app/router.tsx`
- Reference: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the real CLI entry points, web routes, dependency versions, Scenario Bank facts, and calibration results already present in the repository.
- Produces: one GitHub-renderable README with copy-paste startup, architecture, evaluation, configuration, and scope documentation.

- [ ] **Step 1: Inventory every public claim before writing**

Confirm in the referenced files that Python is `>=3.11`, LangGraph is `>=0.2`, React is `^19.1.1`, the nested workflow documents `uv sync --frozen --dev` and `uv run pytest -q`, the backend factory is `profile_agent.web.app:create_app`, and frontend routes include `/assessments/new` and `/demo/assessment`. Because the workflow is not located at the outer Git root, do not present it as an active GitHub Actions status badge.

- [ ] **Step 2: Replace the README using the approved information architecture**

Write these sections in this order: title and positioning; factual badge bar; product flow; implemented capabilities; two-path quickstart; enterprise usage and demo routes; adaptive interview mechanism; Scenario Module RAG; evidence-based scoring; architecture; calibration and tests; configuration; tech stack; collapsed project structure; FAQ; current scope. Use Mermaid for the product and technical flows. Remove the old chronological “这版优化” development-log narrative.

- [ ] **Step 3: Verify copy-paste commands and links**

Run the README's offline Python verification command, frontend tests, and frontend production build using the existing environments. Check every relative Markdown link resolves on disk and every repository/badge URL is syntactically valid. If a command cannot run because a required executable is absent, correct the README if necessary and report the exact external prerequisite rather than claiming success.

- [ ] **Step 4: Review factual and presentation quality**

Compare every number and identifier against the design spec and repository. Confirm no License/Docker Pulls badge, no API key, no unsupported multi-role claim, no placeholder, exactly two quickstart paths, and GitHub-compatible Mermaid/details/table syntax. Report the exact commands and outcomes.

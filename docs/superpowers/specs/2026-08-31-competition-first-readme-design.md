# Competition-First README Design

## Objective

Rewrite the repository README for competition judges and enterprise hiring users first, while preserving a verifiable developer path to run and inspect the project.

## Positioning

The project name presented to readers is **衡鉴 Evidence Hiring**. It is an enterprise-facing AI Agent competency assessment system focused on the `AI application engineering / 2026-H2` role pack. The opening must explain the complete product outcome in one pass:

```text
JD + resume
→ reviewable interview plan
→ evidence-driven adaptive interview
→ traceable competency report
```

The README must not describe the repository as a generic multi-role recruitment platform. It must distinguish the real assessment path from the built-in report demonstration.

## Audience Order

1. Competition judges evaluating interview depth, report quality, human-like interaction, RAG, memory, and technical implementation.
2. Enterprise recruiters and interviewers deciding whether the workflow is understandable and useful.
3. Developers who need copy-paste commands, architecture boundaries, configuration, tests, and calibration entry points.

## Information Architecture

The README uses an inverted pyramid:

1. Project name, one-line positioning, and 3–5 factual badges.
2. Product flow and implemented capabilities.
3. Exactly two quickstart paths: `uv` as the fastest path and `venv + pip` as the controllable local-development path.
4. Real enterprise usage path and built-in demo entry.
5. Adaptive interview mechanism: Planner, Supervisor, Scenario RAG, QuestionGenerator, AnswerProcessor, Evidence, and report.
6. Scenario Module RAG and evidence-based scoring, including reproducible calibration facts.
7. Architecture, configuration, evaluation commands, tech stack, collapsed project structure, FAQ, and project status.

## Factual Badges

The initial badge bar may include only facts currently backed by the repository:

- Python `3.11+`
- LangGraph `0.2+`
- React `19`
- FastAPI `0.116+`

Do not add License or Docker Pulls badges because the repository currently contains neither a license file nor a published Docker image. The workflow file now lives at the Git repository root and is eligible to run in GitHub Actions. Do not invent coverage, stars, downloads, or deployment status.

## Verified Project Facts

- GitHub repository: `https://github.com/hr-huang/AI_Interview`
- The application is stored directly at the checked-out Git repository root; the GitHub landing README and runtime manifests live at that same level.
- Backend package: `keda-profile-agent`
- Backend application factory: `profile_agent.web.app:create_app`
- Backend development port: `8000`
- Frontend package manager: `pnpm@11.19.0`
- Frontend command: `pnpm --dir web dev`
- Frontend development URL: `http://127.0.0.1:5173`
- Real assessment route: `/assessments/new`
- Demonstration report route: `/demo/assessment`
- Runtime focus: `ai_application_engineering / 2026-H2`
- Scenario bank: 10 scenarios, 35 retrieval modules, 38 hidden constraints.
- Frozen calibration: 24 cases; Top-1 acceptable 24/24; Top-3 recall 24/24; forbidden Top-1 0; fallback 0. The seven Top-3 forbidden diagnostics must be disclosed as diagnostics rather than hidden.
- Test evidence at the design freeze: 868 tests passed with 827 subtests; the README must give the reproduction command and avoid implying those numbers are a permanent badge.

## Content Rules

- Chinese is the primary language; retain precise English identifiers where they are code or industry terms.
- Use direct, enterprise-readable language. Avoid “强大、领先、一站式、赋能”等 adjective-driven claims.
- Explain why each layer exists with one concrete example where useful.
- Keep long configuration and directory references inside `<details>`.
- Use Mermaid for product flow and technical architecture.
- Never expose real API keys or copy values from `.env`.
- State that real LLM, embedding, and reranker calls may incur provider fees.
- State that unavailable optional retrieval services fall back to reviewed JSON scenarios.
- Clearly mark current scope and unfinished expansion instead of implying unsupported job families or production deployment.
- State that the backend interview runtime exists while the React candidate interview page is still an entry shell.

## Verification

Before completion:

1. Verify every referenced file, command, route, version, count, and metric against the repository.
2. Check all relative Markdown links resolve.
3. Check badge URLs and repository URLs are syntactically valid.
4. Run the backend offline test suite and frontend test/build commands when dependencies are available.
5. Review the rendered structure for GitHub-compatible tables, details blocks, and Mermaid syntax.

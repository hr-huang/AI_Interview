# Interview Portfolio README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the repository homepage into an evidence-first technical interview portfolio with real product screenshots, a verified dynamic-follow-up trace, reproducible run instructions, and the approved Chinese roadmap.

**Architecture:** `docs/README_PORTFOLIO_DESIGN_SPEC.md` is the single source of truth. Product visuals are captured from the real React pages with frozen anonymous data or deterministic local fixtures; README claims are backed by source, tests, or reproducible commands. No business logic, paid-provider behavior, model prompt, scoring rule, or runtime contract changes are part of this work.

**Tech Stack:** GitHub Markdown, HTML tables, Mermaid, React/Vite pages, FastAPI frozen demo endpoint, Playwright/browser screenshots, PowerShell verification.

## Global Constraints

- Work only in `D:\AI_Interview-worktrees\readme-portfolio` on `codex/readme-portfolio`.
- Keep `D:\AI_Interview` on `main` unchanged.
- Use `docs/README_PORTFOLIO_DESIGN_SPEC.md` as the only README design specification.
- Capture at `1440 × 900` or `1600 × 1000`; each PNG should target `< 700 KB` and remain `< 1 MB`.
- Never expose an API key, candidate token, real name, email, phone, private JD, local absolute path, or sensitive URL.
- Do not call a paid LLM, embedding provider, reranker, or remote vector database while producing documentation.
- Do not invent a candidate-facing follow-up sentence from `GeneratorSpy`; label the closed loop as a graph integration test with controlled structured-model output.
- Use exactly five required assets under `docs/assets/readme/`: `hero-report.png`, `workflow-create.png`, `workflow-plan.png`, `workflow-interview.png`, and `workflow-report-evidence.png`.
- GIF is optional and is not part of acceptance.
- Roadmap stays near the end; completed items move out of Roadmap rather than accumulating checked boxes.

---

### Task 1: Consolidate the design source and approved Roadmap

**Files:**
- Delete: `docs/superpowers/specs/2026-09-01-interview-portfolio-readme-design.md`
- Modify: `docs/README_PORTFOLIO_DESIGN_SPEC.md`

**Interfaces:**
- Consumes: the approved Chinese Roadmap text from the user.
- Produces: one canonical design spec that the README and screenshot tasks follow.

- [ ] **Step 1: Remove the superseded short specification**

Delete only `docs/superpowers/specs/2026-09-01-interview-portfolio-readme-design.md`; the complete `docs/README_PORTFOLIO_DESIGN_SPEC.md` remains canonical.

- [ ] **Step 2: Add Roadmap rules to the canonical specification**

Add a section after Delivery Boundary that requires these groups and items:

```text
Agent 能力
  长期记忆
  场景库持续更新
  Agent 可观测性与回放
  外部证据验证工具

产品能力
  企业工作台
  Role Pack 扩展
  语音与虚拟数字人面试

部署与安全
  企业认证与租户隔离
  候选人邀请链接治理
  模型密钥持久化
  生产数据基础设施
  公开演示与部署
```

Preserve the user's concrete descriptions and safety boundaries. State that completed items are removed from Roadmap and moved to implemented capabilities or release history.

- [ ] **Step 3: Verify the design documents are unambiguous**

Run:

```powershell
Select-String -Path docs/README_PORTFOLIO_DESIGN_SPEC.md -Pattern '长期记忆|场景库持续更新|Agent 可观测性与回放|外部证据验证工具|企业工作台|Role Pack 扩展|语音与虚拟数字人面试|企业认证与租户隔离|公开演示与部署'
git diff --check
```

Expected: every approved Roadmap item is found and `git diff --check` reports no errors.

- [ ] **Step 4: Commit the canonical specification**

```powershell
git add docs/README_PORTFOLIO_DESIGN_SPEC.md docs/superpowers/specs/2026-09-01-interview-portfolio-readme-design.md docs/superpowers/plans/2026-09-02-interview-portfolio-readme.md
git commit -m "docs: finalize portfolio readme roadmap"
```

---

### Task 2: Capture and sanitize the five product assets

**Files:**
- Create: `docs/assets/readme/hero-report.png`
- Create: `docs/assets/readme/workflow-create.png`
- Create: `docs/assets/readme/workflow-plan.png`
- Create: `docs/assets/readme/workflow-interview.png`
- Create: `docs/assets/readme/workflow-report-evidence.png`

**Interfaces:**
- Consumes: existing React routes and the frozen `/api/demo/assessment` payload; plan/interview screenshots may use deterministic anonymous API fixtures but must render the real production components.
- Produces: five sanitized relative assets referenced by `README.md`.

- [ ] **Step 1: Start the local documentation capture environment**

Run the real backend and frontend without invoking a model:

```powershell
uv run uvicorn profile_agent.web.app:create_app --factory --host 127.0.0.1 --port 8000
pnpm --dir web dev --host 127.0.0.1
```

Expected: `/demo/assessment` loads through the frozen provider-free endpoint.

- [ ] **Step 2: Capture the report hero**

At a `1440 × 900` viewport, capture `/demo/assessment` with candidate overview, enterprise decision, coverage/confidence, and the top of the radar visible. Save as `hero-report.png`. Do not open Evidence Drawer in this image.

- [ ] **Step 3: Capture the four workflow stages**

Capture:

```text
/assessments/new                  → workflow-create.png
/assessments/readme-plan-demo/plan → workflow-plan.png
/interviews/readme-active-demo     → workflow-interview.png
/demo/assessment + open evidence  → workflow-report-evidence.png
```

The create page uses synthetic public JD/resume text. The plan page shows Candidate Signal, Claim, dimensions, Target, and Evidence Requirement. The interview page shows an active Agent engineering question and answer box, not the completion page. The evidence report shows one excerpt linked to a transcript turn.

- [ ] **Step 4: Verify asset dimensions, size, and secrets**

Run a read-only image metadata check and OCR/text inspection. Confirm:

```text
5 files exist
each width is 1440 or 1600 CSS capture size
each file < 1 MB
no sk- key
no candidate token
no real name/email/phone
no D:\ or C:\ path
```

- [ ] **Step 5: Commit the product assets**

```powershell
git add docs/assets/readme
git commit -m "docs: add portfolio product screenshots"
```

---

### Task 3: Rewrite README around product evidence

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the canonical spec and five required assets.
- Produces: the GitHub repository landing page for technical interviewers.

- [ ] **Step 1: Replace the current information hierarchy**

Use this order:

```text
Hero + report screenshot
一次评估如何完成（2 × 2 screenshot table）
我解决的不是出题，而是验证链路（Problem → Design → Proof）
为什么上一轮回答会改变下一题（verified integration trace）
One canonical architecture diagram
Measured retrieval and test evidence
Exactly two local run paths
Technology and deeper documentation
Current delivery boundary
Approved Chinese Roadmap
License status
```

- [ ] **Step 2: Add the verified dynamic-follow-up trace**

Use only this verified trace:

```text
Memory 写入 / 删除边界 · role_dim_03
→ missing_evidence_tags = ["版本", "引用"]
→ latest_gap_tags = ["版本", "引用"]
→ selected constraint = knowledge_policy_version_stale
→ not selected = knowledge_memory_delete
```

Label it as a graph integration test using controlled structured output. Do not add an invented natural-language next question.

- [ ] **Step 3: Add the approved Chinese Roadmap verbatim in meaning**

Include all headings, checklist items, memory categories, scenario update chain, observability fields, tool boundaries, product expansion items, and deployment/security items approved by the user. Keep it near the end and do not mark unfinished work as completed.

- [ ] **Step 4: Keep progressive disclosure**

Move full environment variables, BYOK details, Scenario Bank maintenance, repository tree, and deployment notes into `<details>` or deep-document links. Avoid duplicate architecture diagrams and framework-name feature lists.

- [ ] **Step 5: Validate Markdown references**

Run:

```powershell
git diff --check
Select-String README.md -Pattern 'docs/assets/readme/hero-report.png|docs/assets/readme/workflow-create.png|docs/assets/readme/workflow-plan.png|docs/assets/readme/workflow-interview.png|docs/assets/readme/workflow-report-evidence.png'
```

Expected: all five relative image paths occur and there are no whitespace errors.

- [ ] **Step 6: Commit README**

```powershell
git add README.md
git commit -m "docs: present project as evidence-first interview portfolio"
```

---

### Task 4: Final verification and rendered review

**Files:**
- Verify: `README.md`
- Verify: `docs/assets/readme/*.png`
- Verify: `docs/README_PORTFOLIO_DESIGN_SPEC.md`

**Interfaces:**
- Consumes: completed documentation and assets.
- Produces: evidence that README claims, links, screenshots, tests, and build remain valid.

- [ ] **Step 1: Run the full automated checks**

```powershell
uv run pytest -q
pnpm --dir web test
pnpm --dir web build
```

Expected: backend, frontend, and production build pass. README records only the current results from this run.

- [ ] **Step 2: Validate the Scenario Bank without provider calls**

```powershell
uv run python run_scenario_bank.py validate
```

Expected: 10 scenarios, 35 retrieval units, and 38 constraints; no provider call.

- [ ] **Step 3: Review GitHub rendering**

Render the README through a GitHub-compatible Markdown preview. Verify the first screen, two-column workflow table, Mermaid diagram, `<details>` blocks, image scaling, and anchor links.

- [ ] **Step 4: Audit the final diff**

```powershell
git status --short
git diff origin/main...HEAD --stat
git diff origin/main...HEAD -- README.md docs/README_PORTFOLIO_DESIGN_SPEC.md
```

Expected: only the README, canonical spec, implementation plan, and README assets are intentional changes; generated calibration artifacts remain uncommitted and are excluded.

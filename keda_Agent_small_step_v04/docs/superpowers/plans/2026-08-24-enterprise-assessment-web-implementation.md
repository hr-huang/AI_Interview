# 企业岗位胜任力评估 Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个企业可输入真实 JD 和候选人材料、审核计划、运行动态面试并查看可追溯胜任力报告的 Web 应用，同时保留零 API 的只读演示报告。

**Architecture:** FastAPI 作为现有 `profile_agent` 的 Web 适配层，SQLite 保存应用状态并使用 `SqliteSaver` 持久化 LangGraph checkpoint；React + TypeScript + Vite 只消费服务端 ViewModel，不在浏览器重新评分。后端按文件接入、计划冻结、面试会话和报告组合四个边界拆分，所有真实模型边界均可注入 Fake 服务完成零 API 测试。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic 2、SQLite、LangGraph SqliteSaver、PyMuPDF、python-docx、RapidOCR + ONNX Runtime、React、TypeScript、Vite、Vitest、Testing Library、Playwright。

## Global Constraints

- 当前只支持 `ai_application_engineering / 2026-H2`，不得增加第二岗位包。
- React 不计算分数、等级、覆盖率、置信度或岗位匹配度。
- `UNVERIFIED` 必须保持 `score=null`，不得显示为 0 或能力不足。
- 企业只能受约束修改验证重点；岗位核心/Gating 目标、Role Pack、Rubric 和评分权重不可编辑。
- `InterviewPlan` 与 `ScoringBlueprint` 必须在候选人开始前共同冻结；开始后不可修改。
- 演示案例必须使用离线 C03 数据，不调用任何真实 LLM provider。
- PDF/DOCX/TXT 原生提取优先，仅低质量页进入本地 OCR；第一版不使用通用视觉语言模型。
- 普通测试禁止真实模型调用；真实模型只允许用户主动执行一次冒烟测试。
- Python 新行为严格执行 RED → GREEN → REFACTOR；React 新行为使用 Vitest/Testing Library 同样执行测试先行。
- 文件和数据默认限制：单文件 5 MiB、PDF 最多 10 页、仅一个简历文件。
- `langgraph-checkpoint-sqlite` 最低使用 `3.0.1`；SQLite checkpointer 仅定位比赛 Demo 和本地小型工作流。
- 视觉使用深海军蓝、纸张灰白与陶土橙；禁止紫色渐变、玻璃拟态和固定六维标签。
- HTTP JSON 字段统一沿用 Python/Pydantic 的 `snake_case`；TypeScript types 不另做 camelCase 映射。

---

### Task 1: Web contracts、状态机与 SQLite repository

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `profile_agent/web/__init__.py`
- Create: `profile_agent/web/schemas.py`
- Create: `profile_agent/web/repository.py`
- Test: `tests/test_web_repository.py`

**Interfaces:**
- Produces: `AssessmentStatus`、`AssessmentRecord`、`SqliteAssessmentRepository`。
- Produces: `transition_assessment(record, target_status)`，后续 API 和会话服务统一使用。
- Persists: 评估 JSON、计划 JSON、报告 JSON、token hash 和 answer idempotency response。

- [ ] **Step 1: 写状态机与 repository 的失败测试**

```python
# tests/test_web_repository.py
import tempfile
import unittest
from pathlib import Path

from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


class WebRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "web.sqlite3"
        self.repo = SqliteAssessmentRepository(self.db_path)

    def tearDown(self) -> None:
        self.repo.close()
        self.temp_dir.cleanup()

    def test_round_trips_assessment_and_rejects_illegal_transition(self) -> None:
        record = AssessmentRecord.new(
            assessment_id="ast_001",
            target_role="AI 应用工程师",
            jd_text="负责 Agent Workflow 落地",
            resume_text="候选人有 LangGraph 项目",
        )
        self.repo.create(record)
        loaded = self.repo.get("ast_001")
        self.assertEqual(loaded.status, AssessmentStatus.DRAFT)

        analyzing = loaded.transition_to(AssessmentStatus.ANALYZING)
        self.repo.save(analyzing)
        self.assertEqual(
            self.repo.get("ast_001").status,
            AssessmentStatus.ANALYZING,
        )
        with self.assertRaisesRegex(ValueError, "非法评估状态转换"):
            analyzing.transition_to(AssessmentStatus.COMPLETE)

    def test_answer_idempotency_returns_first_response(self) -> None:
        response = {"state": "waiting", "turn_id": "turn_001"}
        self.repo.save_answer_response("token_hash", "idem_1", response)
        self.assertFalse(
            self.repo.save_answer_response(
                "token_hash", "idem_1", {"state": "different"}
            )
        )
        self.assertEqual(
            self.repo.get_answer_response("token_hash", "idem_1"),
            response,
        )

    def test_candidate_token_hash_is_indexed_and_rotatable(self) -> None:
        record = AssessmentRecord.new(
            assessment_id="ast_token",
            target_role="AI 应用工程师",
            jd_text="Agent Workflow",
            resume_text="LangGraph 项目",
        ).model_copy(update={"candidate_token_hash": "hash_v1"})
        self.repo.create(record)
        self.assertEqual(
            self.repo.get_by_candidate_token_hash("hash_v1").id,
            "ast_token",
        )
        self.repo.save(record.model_copy(update={"candidate_token_hash": "hash_v2"}))
        with self.assertRaises(KeyError):
            self.repo.get_by_candidate_token_hash("hash_v1")
        self.assertEqual(
            self.repo.get_by_candidate_token_hash("hash_v2").id,
            "ast_token",
        )
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_web_repository -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'profile_agent.web'`。

- [ ] **Step 3: 增加 Web 依赖和数据忽略规则**

```toml
# pyproject.toml dependencies 追加
"fastapi>=0.116",
"uvicorn[standard]>=0.35",
"python-multipart>=0.0.20",
"httpx>=0.28",
"pymupdf>=1.26",
"python-docx>=1.2",
"rapidocr>=3.9.0",
"onnxruntime>=1.22",
"langgraph-checkpoint-sqlite>=3.0.1",
```

```gitignore
# .gitignore 追加
data/
web/node_modules/
web/dist/
web/playwright-report/
web/test-results/
```

Run: `uv lock && uv sync`

Expected: dependency resolution succeeds and `.venv\Scripts\python.exe` imports FastAPI, PyMuPDF and `langgraph.checkpoint.sqlite`。

- [ ] **Step 4: 实现状态模型**

```python
# profile_agent/web/schemas.py
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AssessmentStatus(StrEnum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    PLAN_REVIEW = "PLAN_REVIEW"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    REPORTING = "REPORTING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS = {
    AssessmentStatus.DRAFT: {AssessmentStatus.ANALYZING},
    AssessmentStatus.ANALYZING: {
        AssessmentStatus.PLAN_REVIEW,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.PLAN_REVIEW: {
        AssessmentStatus.READY,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.READY: {AssessmentStatus.IN_PROGRESS},
    AssessmentStatus.IN_PROGRESS: {
        AssessmentStatus.REPORTING,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.REPORTING: {
        AssessmentStatus.COMPLETE,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.COMPLETE: set(),
    AssessmentStatus.FAILED: {
        AssessmentStatus.ANALYZING,
        AssessmentStatus.PLAN_REVIEW,
        AssessmentStatus.IN_PROGRESS,
        AssessmentStatus.REPORTING,
    },
}


class AssessmentRecord(BaseModel):
    id: str
    status: AssessmentStatus
    target_role: str
    jd_text: str
    resume_text: str
    pre_interview_state: dict[str, Any] | None = None
    original_plan: dict[str, Any] | None = None
    final_plan: dict[str, Any] | None = None
    scoring_blueprint: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    candidate_token_hash: str | None = None
    failed_stage: str | None = None
    error_message: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)

    @classmethod
    def new(cls, *, assessment_id: str, target_role: str, jd_text: str, resume_text: str) -> "AssessmentRecord":
        now = datetime.now(timezone.utc)
        return cls(
            id=assessment_id,
            status=AssessmentStatus.DRAFT,
            target_role=target_role.strip(),
            jd_text=jd_text.strip(),
            resume_text=resume_text.strip(),
            created_at=now,
            updated_at=now,
        )

    def transition_to(self, status: AssessmentStatus) -> "AssessmentRecord":
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"非法评估状态转换: {self.status} -> {status}")
        return self.model_copy(update={
            "status": status,
            "updated_at": datetime.now(timezone.utc),
            "version": self.version + 1,
        })
```

- [ ] **Step 5: 实现 SQLite repository**

```python
# profile_agent/web/repository.py
import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from profile_agent.web.schemas import AssessmentRecord


class SqliteAssessmentRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS assessments (
            id TEXT PRIMARY KEY,
            candidate_token_hash TEXT UNIQUE,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS answer_requests (
            token_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            response_json TEXT NOT NULL,
            PRIMARY KEY (token_hash, idempotency_key)
        );
        """)

    def create(self, record: AssessmentRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO assessments(id, candidate_token_hash, payload) VALUES (?, ?, ?)",
                (record.id, record.candidate_token_hash, record.model_dump_json()),
            )

    def get(self, assessment_id: str) -> AssessmentRecord:
        row = self._conn.execute(
            "SELECT payload FROM assessments WHERE id = ?", (assessment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(assessment_id)
        return AssessmentRecord.model_validate_json(row["payload"])

    def get_by_candidate_token_hash(self, token_hash: str) -> AssessmentRecord:
        row = self._conn.execute(
            "SELECT payload FROM assessments WHERE candidate_token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            raise KeyError(token_hash)
        return AssessmentRecord.model_validate_json(row["payload"])

    def save(self, record: AssessmentRecord) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE assessments SET candidate_token_hash = ?, payload = ? WHERE id = ?",
                (record.candidate_token_hash, record.model_dump_json(), record.id),
            )
            if cursor.rowcount != 1:
                raise KeyError(record.id)

    def save_answer_response(self, token_hash: str, key: str, response: dict[str, Any]) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO answer_requests VALUES (?, ?, ?)",
                (token_hash, key, json.dumps(response, ensure_ascii=False)),
            )
            return cursor.rowcount == 1

    def get_answer_response(self, token_hash: str, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT response_json FROM answer_requests WHERE token_hash=? AND idempotency_key=?",
            (token_hash, key),
        ).fetchone()
        return None if row is None else json.loads(row["response_json"])

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 6: 验证 GREEN 并运行全部 Python 测试**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_web_repository -v`

Expected: PASS。

Run: `\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: existing suite and new tests pass。

- [ ] **Step 7: 提交**

```powershell
git add pyproject.toml uv.lock .gitignore profile_agent/web tests/test_web_repository.py
git commit -m "feat: add web assessment repository"
```

---

### Task 2: PDF、DOCX、TXT 与 OCR 文件接入

**Files:**
- Create: `profile_agent/web/document_ingestion.py`
- Test: `tests/test_document_ingestion.py`

**Interfaces:**
- Produces: `DocumentExtractor.extract(filename: str, content: bytes) -> ExtractedDocument`。
- Consumes: injectable `OcrEngine.recognize(image_bytes: bytes) -> list[OcrLine]`。
- Guarantees: `used_ocr_pages` 只包含原生提取未达到质量门槛的页。

- [ ] **Step 1: 写失败测试，覆盖原生提取和 OCR 路由**

```python
# tests/test_document_ingestion.py
import io
import unittest

import pymupdf
from docx import Document

from profile_agent.web.document_ingestion import DocumentExtractor, OcrLine


class FakeOcr:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image_bytes: bytes) -> list[OcrLine]:
        self.calls += 1
        return [OcrLine(text="扫描页中的 LangGraph 项目", confidence=0.98, y=10)]


class DocumentIngestionTest(unittest.TestCase):
    def test_text_pdf_does_not_use_ocr(self) -> None:
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((50, 50), "AI Agent Workflow LangGraph FastAPI project experience")
        content = pdf.tobytes()
        ocr = FakeOcr()

        result = DocumentExtractor(ocr_engine=ocr).extract("resume.pdf", content)

        self.assertIn("LangGraph", result.text)
        self.assertEqual(result.used_ocr_pages, [])
        self.assertEqual(ocr.calls, 0)

    def test_blank_pdf_uses_ocr_for_only_blank_page(self) -> None:
        pdf = pymupdf.open()
        pdf.new_page()
        result = DocumentExtractor(ocr_engine=FakeOcr()).extract(
            "scan.pdf", pdf.tobytes()
        )
        self.assertIn("扫描页", result.text)
        self.assertEqual(result.used_ocr_pages, [1])

    def test_docx_reads_paragraphs_and_tables(self) -> None:
        stream = io.BytesIO()
        doc = Document()
        doc.add_paragraph("候选人简介")
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "Agent 项目"
        doc.save(stream)
        result = DocumentExtractor(ocr_engine=FakeOcr()).extract(
            "resume.docx", stream.getvalue()
        )
        self.assertIn("候选人简介", result.text)
        self.assertIn("Agent 项目", result.text)

    def test_old_doc_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "DOCX 或 PDF"):
            DocumentExtractor(ocr_engine=FakeOcr()).extract("resume.doc", b"x")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_document_ingestion -v`

Expected: FAIL because `document_ingestion` does not exist。

- [ ] **Step 3: 实现提取契约、内容签名和质量门槛**

```python
# profile_agent/web/document_ingestion.py
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Protocol

import pymupdf
from docx import Document

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 10


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    y: float


class OcrEngine(Protocol):
    def recognize(self, image_bytes: bytes) -> list[OcrLine]: ...


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    file_type: str
    used_ocr_pages: list[int]


def _usable(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return len(compact) >= 30 and text.count("�") / max(1, len(text)) < 0.05


class RapidOcrEngine:
    def __init__(self) -> None:
        from rapidocr import RapidOCR
        self._engine = RapidOCR()

    def recognize(self, image_bytes: bytes) -> list[OcrLine]:
        result = self._engine(image_bytes)
        lines = []
        boxes = [] if result.boxes is None else result.boxes
        texts = () if result.txts is None else result.txts
        scores = () if result.scores is None else result.scores
        for box, text, confidence in zip(boxes, texts, scores):
            y = min(point[1] for point in box)
            lines.append(OcrLine(text=text, confidence=float(confidence), y=float(y)))
        return sorted(lines, key=lambda line: line.y)
```

Implementation requirements for `DocumentExtractor`:

```python
class DocumentExtractor:
    def __init__(self, ocr_engine: OcrEngine | None = None) -> None:
        self._ocr_engine = ocr_engine

    @property
    def ocr_engine(self) -> OcrEngine:
        # Native PDF/DOCX/TXT paths must not load OCR models.
        if self._ocr_engine is None:
            self._ocr_engine = RapidOcrEngine()
        return self._ocr_engine

    def extract(self, filename: str, content: bytes) -> ExtractedDocument:
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValueError("简历文件为空或超过 5 MiB")
        suffix = filename.lower().rsplit(".", 1)[-1]
        if suffix == "doc":
            raise ValueError("旧版 DOC 不支持，请转换为 DOCX 或 PDF")
        if content.startswith(b"%PDF-"):
            return self._extract_pdf(content)
        if content.startswith(b"PK") and self._is_docx(content):
            return self._extract_docx(content)
        if suffix == "txt":
            return self._extract_txt(content)
        raise ValueError("仅支持 PDF、DOCX 或 TXT 简历")

    @staticmethod
    def _is_docx(content: bytes) -> bool:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return "[Content_Types].xml" in archive.namelist() and "word/document.xml" in archive.namelist()

    def _extract_pdf(self, content: bytes) -> ExtractedDocument:
        document = pymupdf.open(stream=content, filetype="pdf")
        if document.page_count > MAX_PDF_PAGES:
            raise ValueError("PDF 最多支持 10 页")
        pages: list[str] = []
        used_ocr_pages: list[int] = []
        for page_number, page in enumerate(document, start=1):
            blocks = page.get_text("blocks", sort=True)
            native = "\n".join(
                str(block[4]).strip()
                for block in blocks
                if len(block) > 6 and block[6] == 0 and str(block[4]).strip()
            )
            if _usable(native):
                pages.append(native)
                continue
            image = page.get_pixmap(dpi=180).tobytes("png")
            lines = [line.text for line in self.ocr_engine.recognize(image) if line.confidence >= 0.5]
            ocr_text = "\n".join(lines)
            if not _usable(ocr_text):
                raise ValueError(f"PDF 第 {page_number} 页无法提取可用文本")
            pages.append(ocr_text)
            used_ocr_pages.append(page_number)
        return ExtractedDocument("\n\n".join(pages), "pdf", used_ocr_pages)

    def _extract_docx(self, content: bytes) -> ExtractedDocument:
        document = Document(io.BytesIO(content))
        parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        parts.extend(
            cell.text.strip()
            for table in document.tables
            for row in table.rows
            for cell in row.cells
            if cell.text.strip()
        )
        text = "\n".join(parts)
        if not _usable(text):
            raise ValueError("DOCX 未提取到可用文本")
        return ExtractedDocument(text, "docx", [])

    def _extract_txt(self, content: bytes) -> ExtractedDocument:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _usable(text):
                return ExtractedDocument(text.strip(), "txt", [])
        raise ValueError("TXT 编码不支持或未提取到可用文本")
```

- [ ] **Step 4: 验证 GREEN、OCR 安装与完整回归**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_document_ingestion -v`

Expected: PASS。

Run: `\.venv\Scripts\rapidocr.exe check`

Expected: `Success! rapidocr is installed correctly!`。

Run: `\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: PASS without provider calls。

- [ ] **Step 5: 提交**

```powershell
git add profile_agent/web/document_ingestion.py tests/test_document_ingestion.py
git commit -m "feat: extract resume files with OCR fallback"
```

---

### Task 3: Planner draft、受约束 PlanOverride 与最终冻结

**Files:**
- Modify: `profile_agent/graphs/pre_interview.py`
- Modify: `profile_agent/schemas/interview_schema.py`
- Create: `profile_agent/services/plan_review_service.py`
- Modify: `tests/test_pre_interview_graph.py`
- Test: `tests/test_plan_review_service.py`

**Interfaces:**
- Produces: `build_pre_interview_graph(include_scoring_blueprint: bool = True)`，保持 CLI 默认兼容。
- Produces: `PlanOverrideSet` and `freeze_reviewed_plan(...) -> tuple[InterviewPlan, ScoringBlueprint]`。
- Produces: `freeze_reviewed_plan(...) -> tuple[InterviewPlan, ScoringBlueprint]`。

- [ ] **Step 1: 写 draft graph 与计划护栏失败测试**

```python
# tests/test_plan_review_service.py
import unittest

from profile_agent.services.plan_review_service import (
    PlanOverrideSet,
    TargetUpdate,
    freeze_reviewed_plan,
)
from tests.test_interview_planner_guards import make_role_profile, make_timed_draft
from profile_agent.services.interview_planner_service import finalize_interview_plan


class PlanReviewServiceTest(unittest.TestCase):
    def test_core_target_cannot_be_removed_or_demoted(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        target = plan.targets[0]
        overrides = PlanOverrideSet(
            target_updates=[TargetUpdate(target_id=target.id, priority="low")]
        )
        with self.assertRaisesRegex(ValueError, "核心目标"):
            freeze_reviewed_plan(plan, overrides, make_role_profile())

    def test_valid_priority_and_duration_update_rebuilds_blueprint(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        target = plan.targets[0]
        overrides = PlanOverrideSet(
            duration_minutes=45,
            target_updates=[TargetUpdate(target_id=target.id, priority="high")],
        )
        final_plan, blueprint = freeze_reviewed_plan(
            plan, overrides, make_role_profile()
        )
        self.assertEqual(final_plan.duration_minutes, 45)
        self.assertEqual(
            {binding.requirement_id for binding in blueprint.bindings},
            {req.id for req in final_plan.targets[0].evidence_requirements},
        )
```

Update `tests/test_pre_interview_graph.py` with:

```python
def test_draft_graph_stops_before_scoring_blueprint(self) -> None:
    from profile_agent.graphs.pre_interview import build_pre_interview_graph
    graph = build_pre_interview_graph(include_scoring_blueprint=False).get_graph()
    self.assertNotIn("scoring_blueprint", graph.nodes)
    self.assertIn(
        ("interview_planner", "__end__"),
        {(edge.source, edge.target) for edge in graph.edges},
    )
```

- [ ] **Step 2: 运行两组测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_plan_review_service tests.test_pre_interview_graph -v`

Expected: FAIL for missing plan review module and missing graph argument。

- [ ] **Step 3: 让 Pre-Interview 支持 draft 模式并保持默认兼容**

Change the graph tail to:

```python
def build_pre_interview_graph(*, include_scoring_blueprint: bool = True):
    # existing nodes and edges through interview_planner stay unchanged
    if include_scoring_blueprint:
        builder.add_node("scoring_blueprint", scoring_blueprint)
        builder.add_edge("interview_planner", "scoring_blueprint")
        builder.add_edge("scoring_blueprint", END)
    else:
        builder.add_edge("interview_planner", END)
    return builder.compile()


pre_interview_graph = build_pre_interview_graph()
pre_interview_draft_graph = build_pre_interview_graph(
    include_scoring_blueprint=False
)
```

- [ ] **Step 4: 定义 override contracts 并实现最终冻结**

```python
# profile_agent/services/plan_review_service.py
from typing import Literal

from pydantic import BaseModel, Field

from profile_agent.schemas.interview_schema import AssessmentTargetDraft, InterviewPlan
from profile_agent.schemas.report_schema import RoleCompetencyProfile, ScoringBlueprint
from profile_agent.services.interview_planner_service import (
    calculate_closing_buffer,
    calculate_max_questions,
)
from profile_agent.services.scoring_blueprint_service import build_scoring_blueprint


class TargetUpdate(BaseModel):
    target_id: str
    priority: Literal["high", "medium", "low"] | None = None
    objective: str | None = None
    time_budget_minutes: int | None = Field(default=None, ge=0)


class PlanOverrideSet(BaseModel):
    duration_minutes: int | None = None
    minimum_transfer_validations: int = Field(default=1, ge=1, le=3)
    target_updates: list[TargetUpdate] = Field(default_factory=list)
    custom_targets: list[AssessmentTargetDraft] = Field(default_factory=list)


def freeze_reviewed_plan(
    original: InterviewPlan,
    overrides: PlanOverrideSet,
    role_profile: RoleCompetencyProfile,
) -> tuple[InterviewPlan, ScoringBlueprint]:
    duration = overrides.duration_minutes or original.duration_minutes
    if duration not in {30, 45, 60}:
        raise ValueError("面试时长只能是 30、45 或 60 分钟")
    updates = {item.target_id: item for item in overrides.target_updates}
    if len(updates) != len(overrides.target_updates):
        raise ValueError("同一 Target 不能重复修改")
    targets = []
    for target in original.targets:
        update = updates.pop(target.id, None)
        if update is None:
            targets.append(target)
            continue
        if target.must_cover and update.priority not in {None, "high"}:
            raise ValueError("核心目标不能降级或删除")
        targets.append(target.model_copy(update={
            key: value for key, value in {
                "priority": update.priority,
                "objective": update.objective.strip() if update.objective else None,
                "time_budget_minutes": update.time_budget_minutes,
            }.items() if value is not None
        }))
    if updates:
        raise ValueError("PlanOverride 引用了不存在的 Target")
    targets.extend(
        _finalize_custom_targets(overrides.custom_targets, role_profile)
    )
    final = original.model_copy(update={
        "duration_minutes": duration,
        "max_questions": calculate_max_questions(duration),
        "closing_buffer_minutes": calculate_closing_buffer(duration),
        "targets": targets,
    })
    transfer_count = sum(
        req.requires_transfer_validation
        for target in final.targets
        for req in target.evidence_requirements
    )
    if transfer_count < overrides.minimum_transfer_validations:
        raise ValueError("最终计划缺少要求的迁移验证")
    if sum(target.time_budget_minutes for target in final.targets) > duration - final.closing_buffer_minutes:
        raise ValueError("最终计划时间预算超过可用时间")
    return final, build_scoring_blueprint(final, role_profile)
```

- [ ] **Step 4a: 用第二个 RED/GREEN cycle 实现企业补充目标**

Add this failing test before changing production code:

```python
def test_custom_target_must_use_existing_role_dimension(self) -> None:
    plan = finalize_interview_plan(make_timed_draft(10), 30)
    custom = make_timed_draft(5).targets[0]
    custom.objective = "客服 Agent 上线应急处置"
    custom.evidence_requirements[0].planned_role_dimension_id = "missing_dim"
    with self.assertRaisesRegex(ValueError, "企业补充目标.*Role Dimension"):
        freeze_reviewed_plan(
            plan,
            PlanOverrideSet(custom_targets=[custom]),
            make_role_profile(),
        )

def test_custom_target_cannot_claim_core_status(self) -> None:
    plan = finalize_interview_plan(make_timed_draft(10), 30)
    custom = make_timed_draft(5).targets[0]
    custom.must_cover = True
    with self.assertRaisesRegex(ValueError, "企业补充目标.*must_cover"):
        freeze_reviewed_plan(
            plan,
            PlanOverrideSet(custom_targets=[custom]),
            make_role_profile(),
        )
```

Run: `\.venv\Scripts\python.exe -m unittest tests.test_plan_review_service.PlanReviewServiceTest.test_custom_target_must_use_existing_role_dimension -v`

Expected: FAIL because custom targets are not validated or appended yet。

Implement a deterministic converter in `plan_review_service.py`:

```python
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AssessmentTargetDraft,
    EvidenceRequirement,
)


def _finalize_custom_targets(
    drafts: list[AssessmentTargetDraft],
    role_profile: RoleCompetencyProfile,
) -> list[AssessmentTarget]:
    valid_dimensions = {item.id for item in role_profile.dimensions}
    targets: list[AssessmentTarget] = []
    for target_index, draft in enumerate(drafts, start=1):
        if draft.must_cover:
            raise ValueError("企业补充目标不能设置 must_cover=True")
        target_id = f"custom_{target_index:02d}"
        requirements: list[EvidenceRequirement] = []
        for requirement_index, requirement in enumerate(
            draft.evidence_requirements, start=1
        ):
            dimension_id = requirement.planned_role_dimension_id
            if dimension_id not in valid_dimensions:
                raise ValueError(
                    "企业补充目标必须映射到现有 Role Dimension: "
                    + str(dimension_id)
                )
            requirements.append(EvidenceRequirement(
                id=f"{target_id}_req_{requirement_index:02d}",
                description=requirement.description,
                planned_role_dimension_id=dimension_id,
                requires_transfer_validation=requirement.requires_transfer_validation,
            ))
        targets.append(AssessmentTarget(
            id=target_id,
            objective=draft.objective,
            target_type=draft.target_type,
            competency_ids=draft.competency_ids,
            evidence_requirements=requirements,
            related_claim_ids=draft.related_claim_ids,
            priority=draft.priority,
            must_cover=False,
            time_budget_minutes=draft.time_budget_minutes,
            preferred_modes=draft.preferred_modes,
        ))
    return targets
```

In `freeze_reviewed_plan`, append `_finalize_custom_targets(overrides.custom_targets, role_profile)` before transfer and time validation. Custom targets cannot set `must_cover=True`, cannot introduce dimensions, and disappear simply by omitting them from the next pre-freeze `PlanOverrideSet`; original Planner targets are never removed.

- [ ] **Step 5: 验证测试、旧 CLI graph 和完整回归**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_plan_review_service tests.test_pre_interview_graph tests.test_scoring_blueprint_node tests.test_interview_planner_guards -v`

Expected: PASS。

Run: `\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add profile_agent/graphs/pre_interview.py profile_agent/schemas/interview_schema.py profile_agent/services/plan_review_service.py tests/test_pre_interview_graph.py tests/test_plan_review_service.py
git commit -m "feat: review and freeze interview plans"
```

---

### Task 4: Assessment create、analysis 与 plan API

**Files:**
- Create: `profile_agent/web/container.py`
- Create: `profile_agent/web/assessment_service.py`
- Create: `profile_agent/web/routers/__init__.py`
- Create: `profile_agent/web/routers/assessments.py`
- Create: `profile_agent/web/app.py`
- Test: `tests/test_assessment_api.py`

**Interfaces:**
- Produces: `create_app(container: WebContainer | None = None) -> FastAPI`。
- Produces endpoints: create/status/plan/overrides/freeze/retry。
- Consumes: `pre_interview_draft_graph` and `freeze_reviewed_plan`。

- [ ] **Step 1: 写失败的 API contract test**

```python
# tests/test_assessment_api.py
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)


class FakeDraftGraph:
    def invoke(self, state):
        from tests.test_interview_report_integration import InterviewReportIntegrationTest
        return {**state, "interview_plan": InterviewReportIntegrationTest().make_plan()}


class AssessmentApiTest(unittest.TestCase):
    def test_create_with_text_reaches_plan_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = WebContainer.for_test(
                repository=SqliteAssessmentRepository(Path(directory) / "web.db"),
                pre_interview_graph=FakeDraftGraph(),
                dispatcher=InlineDispatcher(),
            )
            client = TestClient(create_app(container))
            response = client.post("/api/assessments", data={
                "target_role": "AI 应用工程师",
                "jd_text": "负责 Agent Workflow",
                "resume_text": "有 LangGraph 项目",
                "idempotency_key": "create_1",
            })
            self.assertEqual(response.status_code, 202)
            assessment_id = response.json()["assessment_id"]
            status = client.get(f"/api/assessments/{assessment_id}")
            self.assertEqual(status.json()["status"], "PLAN_REVIEW")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_assessment_api -v`

Expected: FAIL for missing app/container/service。

- [ ] **Step 3: 实现可注入 container 和 analysis service**

```python
# profile_agent/web/container.py
from dataclasses import dataclass
from pathlib import Path

from profile_agent.graphs.pre_interview import pre_interview_draft_graph
from profile_agent.web.document_ingestion import DocumentExtractor
from profile_agent.web.repository import SqliteAssessmentRepository


@dataclass
class WebContainer:
    repository: SqliteAssessmentRepository
    pre_interview_graph: object
    document_extractor: DocumentExtractor
    dispatcher: object

    @classmethod
    def for_test(
        cls,
        *,
        repository,
        pre_interview_graph,
        dispatcher,
        document_extractor=None,
    ):
        return cls(
            repository,
            pre_interview_graph,
            document_extractor or DocumentExtractor(),
            dispatcher,
        )
```

`AssessmentService.analyze(assessment_id)` must:

1. load DRAFT record and transition to ANALYZING;
2. invoke the draft graph with `resume_text`, `jd_text`, `target_role`, and default `interview_duration_minutes=45`;
3. serialize every Pydantic object with `model_dump(mode="json")`;
4. save original plan and required pre-interview state;
5. transition to PLAN_REVIEW;
6. on handled provider/value errors, save FAILED with `failed_stage="ANALYZING"` and a safe message.

- [ ] **Step 4: 实现 FastAPI app factory and assessment router**

```python
# profile_agent/web/app.py
from fastapi import FastAPI
from profile_agent.web.routers.assessments import router as assessments_router


def create_app(container=None) -> FastAPI:
    app = FastAPI(title="衡鉴 Evidence Hiring")
    app.state.container = container or build_default_container()
    app.include_router(assessments_router, prefix="/api")
    return app
```

The multipart endpoint accepts either `resume_text` or `resume_file`, never neither or both. Return HTTP 202 with `assessment_id` and status. File extraction occurs before creating the record so invalid files cannot create unusable assessment rows. Use a UUID-derived `ast_<hex>` ID and persist the cleaned text, not the original filename.

- [ ] **Step 5: 增加 plan override、freeze 和 retry API tests then implementation**

Add tests proving:

```python
self.assertEqual(client.get(f"/api/assessments/{id}/plan").status_code, 200)
self.assertEqual(
    client.put(f"/api/assessments/{id}/plan-overrides", json={
        "duration_minutes": 45,
        "target_updates": [],
    }).status_code,
    200,
)
freeze = client.post(f"/api/assessments/{id}/freeze")
self.assertEqual(freeze.status_code, 200)
self.assertIn("candidate_url", freeze.json())
```

Freeze must generate a 32-byte `secrets.token_urlsafe` token only after plan and Blueprint validation succeed, store `sha256(token)` in the record, and return `/interviews/<raw token>` once.

- [ ] **Step 6: 验证 API tests and full regression**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_assessment_api -v`

Expected: PASS。

Run: `\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add profile_agent/web tests/test_assessment_api.py
git commit -m "feat: expose assessment planning API"
```

---

### Task 5: 持久化候选人面试与 answer 幂等

**Files:**
- Create: `profile_agent/web/interview_service.py`
- Create: `profile_agent/web/routers/interviews.py`
- Modify: `profile_agent/web/app.py`
- Modify: `profile_agent/web/container.py`
- Test: `tests/test_interview_api.py`

**Interfaces:**
- Produces: `GET /api/interviews/{token}` (read only)。
- Produces: `POST /api/interviews/{token}/start` (first graph invoke)。
- Produces: `POST /api/interviews/{token}/answers` (Command resume with idempotency)。
- Consumes: shared `build_interview_graph(checkpointer=SqliteSaver(...))`。

- [ ] **Step 1: 写失败测试：GET 不启动、POST 才启动、重复答案只处理一次**

```python
# tests/test_interview_api.py
def test_get_does_not_start_and_duplicate_answer_is_idempotent(self) -> None:
    ready = self.client.get(f"/api/interviews/{self.token}")
    self.assertEqual(ready.json()["state"], "ready")
    self.assertEqual(self.graph.invoke_count, 0)

    started = self.client.post(f"/api/interviews/{self.token}/start")
    self.assertEqual(started.json()["state"], "waiting_for_answer")
    turn_id = started.json()["turn"]["id"]

    payload = {
        "turn_id": turn_id,
        "answer": "我会使用 checkpoint 与幂等键",
        "idempotency_key": "answer_1",
    }
    first = self.client.post(f"/api/interviews/{self.token}/answers", json=payload)
    duplicate = self.client.post(f"/api/interviews/{self.token}/answers", json=payload)
    self.assertEqual(first.json(), duplicate.json())
    self.assertEqual(self.graph.resume_count, 1)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_interview_api -v`

Expected: FAIL for missing router/service。

- [ ] **Step 3: 构建 SQLite checkpointer and graph once per app**

```python
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

connection = sqlite3.connect(
    data_dir / "checkpoints.sqlite3",
    check_same_thread=False,
)
checkpointer = SqliteSaver(connection)
interview_graph = build_interview_graph(checkpointer=checkpointer)
```

The app container owns and closes the connection on lifespan shutdown. Use `assessment_id` as `thread_id`; token lookup must hash the provided token and compare with `candidate_token_hash`.

- [ ] **Step 4: 实现 read/start/answer service**

```python
class InterviewService:
    def get_session(self, token: str) -> dict:
        record = self._assessment_for_token(token)
        if record.status == AssessmentStatus.READY:
            return {"state": "ready", "target_role": record.target_role}
        snapshot = self.graph.get_state(self._config(record.id)).values
        return self._public_state(record, snapshot)

    def start(self, token: str) -> dict:
        record = self._assessment_for_token(token)
        if record.status != AssessmentStatus.READY:
            return self.get_session(token)
        initial = self._deserialize_frozen_state(record)
        result = self.graph.invoke(initial, self._config(record.id))
        self.repository.save(record.transition_to(AssessmentStatus.IN_PROGRESS))
        return self._public_state(record, result)

    def answer(self, token: str, request: AnswerRequest) -> dict:
        token_hash = self._hash(token)
        cached = self.repository.get_answer_response(token_hash, request.idempotency_key)
        if cached is not None:
            return cached
        record = self._assessment_for_token(token)
        snapshot = self.graph.get_state(self._config(record.id)).values
        if snapshot.get("current_turn_id") != request.turn_id:
            raise StaleTurnError(snapshot.get("current_turn_id"))
        result = self.graph.invoke(Command(resume=request.answer), self._config(record.id))
        response = self._public_state(record, result)
        self.repository.save_answer_response(token_hash, request.idempotency_key, response)
        return response
```

`_public_state` returns only target role, elapsed time, public phase, turns with question/answer, and current turn. It must never return Evidence, score, next_action reason, claim registry, or assessment report.

- [ ] **Step 5: 完成状态与报告保存**

When a graph result has no interrupt and contains `assessment_report`, transition `IN_PROGRESS -> REPORTING -> COMPLETE`, save the report JSON, and return `{state: "complete"}`. If the graph raises before committing an answer response, do not write the idempotency row; the client may retry with the same key.

- [ ] **Step 6: 验证 restart recovery using a second graph instance**

Add a test that starts a session, closes the first `SqliteSaver` connection, opens another connection to the same file, builds a new graph, and verifies the same `current_turn_id` is returned without generating another question.

Run: `\.venv\Scripts\python.exe -m unittest tests.test_interview_api -v`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add profile_agent/web tests/test_interview_api.py
git commit -m "feat: serve persistent interview sessions"
```

---

### Task 6: ReportViewModel 与零 API demo endpoint

**Files:**
- Create: `profile_agent/web/report_view.py`
- Create: `profile_agent/web/demo_service.py`
- Create: `profile_agent/web/routers/demo.py`
- Modify: `profile_agent/web/routers/assessments.py`
- Modify: `profile_agent/web/app.py`
- Test: `tests/test_report_view.py`
- Test: `tests/test_demo_api.py`

**Interfaces:**
- Produces: `build_report_view(report, plan, turns, evidences, profile, *, demo) -> ReportViewModel`。
- Produces: `GET /api/assessments/{id}/report` and `GET /api/demo/assessment`。

- [ ] **Step 1: 写失败测试，证明原因能回到原始问答且 UNVERIFIED 保留 null**

```python
# tests/test_report_view.py
import unittest
from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.report_view import build_report_view


class ReportViewTest(unittest.TestCase):
    def test_reason_links_to_question_answer_and_evidence(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        limiting = [
            reason
            for dimension in view.radar_dimensions
            for reason in dimension.reasons
            if "ev_C03_002" in reason.evidence_ids
        ]
        self.assertTrue(limiting)
        self.assertIn("受监管", limiting[0].sources[0].answer)
        self.assertIn("迁移", limiting[0].sources[0].question)

    def test_unverified_dimension_keeps_score_none(self) -> None:
        case = get_report_calibration_case("C06")
        run = run_offline_calibration_case(case)
        view = build_report_view(run.report, case.plan, case.turns, case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"), demo=True)
        self.assertTrue(any(item.level == "UNVERIFIED" and item.score is None for item in view.radar_dimensions))
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_report_view -v`

Expected: FAIL for missing module。

- [ ] **Step 3: 定义 focused ViewModel and deterministic join**

```python
class EvidenceSourceView(BaseModel):
    evidence_id: str
    turn_id: str
    question: str
    answer: str
    observation: str
    source_excerpt: str


class ReasonView(BaseModel):
    reason_type: str
    text: str
    evidence_ids: list[str]
    rubric_signal_ids: list[str]
    sources: list[EvidenceSourceView]


class RadarDimensionView(BaseModel):
    dimension_id: str
    name: str
    score: float | None
    level: str
    coverage: float
    confidence: str
    reasons: list[ReasonView]


class ReportViewModel(BaseModel):
    demo: bool
    target_role: str
    role_profile_version: str
    scoring_engine_version: str
    job_match: dict
    radar_dimensions: list[RadarDimensionView]
    narrative: dict
    interview_path: list[dict]
    claim_verifications: list[dict]
    assessment_limitations: list[str]
```

Build indexes by `turn.id` and `evidence.id`; raise on dangling IDs instead of silently dropping them. Preserve Role Pack dimension order. Resolve rubric readable text from minimum/excellence/error/alternative collections without changing the locked IDs.

- [ ] **Step 4: 实现 zero-API demo from C03**

```python
def build_demo_report_view() -> ReportViewModel:
    case = get_report_calibration_case("C03")
    run = run_offline_calibration_case(case)
    return build_report_view(
        run.report,
        case.plan,
        case.turns,
        case.evidences,
        load_role_profile("ai_application_engineering", "2026-H2"),
        demo=True,
    )
```

Patch every public LLM boundary to raise in `tests/test_demo_api.py`; `GET /api/demo/assessment` must still return 200. This proves the demo never calls a provider.

- [ ] **Step 5: 验证 tests and regression**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_report_view tests.test_demo_api -v`

Expected: PASS。

Run: `\.venv\Scripts\python.exe run_offline_calibration.py --case ALL`

Expected: C01, C03, C06 PASS。

- [ ] **Step 6: 提交**

```powershell
git add profile_agent/web tests/test_report_view.py tests/test_demo_api.py
git commit -m "feat: expose traceable report views"
```

---

### Task 7: React/Vite foundation、API client 与视觉 tokens

**Files:**
- Create: `web/package.json`
- Create: `web/pnpm-lock.yaml`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/app/App.tsx`
- Create: `web/src/app/router.tsx`
- Create: `web/src/api/client.ts`
- Create: `web/src/api/types.ts`
- Create: `web/src/styles/tokens.css`
- Create: `web/src/styles/global.css`
- Create: `web/src/test/setup.ts`
- Test: `web/src/app/App.test.tsx`

**Interfaces:**
- Produces: browser routes matching the approved design。
- Produces: typed `api` functions; components never call `fetch` directly。
- Produces: CSS variables for the “数据审计版” visual system。

- [ ] **Step 1: Scaffold package and install dependencies**

```powershell
New-Item -ItemType Directory -Force web | Out-Null
Set-Location web
pnpm init
pnpm add react react-dom react-router-dom
pnpm add -D vite typescript @vitejs/plugin-react vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @types/react @types/react-dom @playwright/test
```

Edit package scripts to:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  }
}
```

- [ ] **Step 2: 写失败的 app shell test**

```tsx
// web/src/app/App.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import { App } from './App'

test('renders enterprise product identity and primary assessment route', () => {
  render(<MemoryRouter initialEntries={['/assessments/new']}><App /></MemoryRouter>)
  expect(screen.getByText('衡鉴 · Evidence Hiring')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '创建岗位胜任力评估' })).toBeInTheDocument()
})
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `pnpm --dir web test`

Expected: FAIL because app files do not exist。

- [ ] **Step 4: Implement router shell and tokens**

```css
/* web/src/styles/tokens.css */
:root {
  --ink-950: #14253b;
  --ink-800: #263c54;
  --paper-50: #f8f8f4;
  --paper-100: #f0f1ed;
  --line-300: #cbd1ce;
  --muted-600: #687378;
  --accent-600: #c96a35;
  --positive-600: #507d70;
  --danger-600: #b84e36;
  --font-display: Georgia, 'Noto Serif SC', serif;
  --font-body: 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
}
```

Routes must be declared in one place:

```tsx
<Route path="/assessments/new" element={<NewAssessmentPage />} />
<Route path="/assessments/:assessmentId/analyzing" element={<AnalyzingPage />} />
<Route path="/assessments/:assessmentId/plan" element={<PlanReviewPage />} />
<Route path="/assessments/:assessmentId/report" element={<ReportPage />} />
<Route path="/interviews/:candidateToken" element={<InterviewPage />} />
<Route path="/demo/assessment" element={<DemoReportPage />} />
```

Initially each page may render its approved heading only; subsequent tasks replace placeholders through tests. `api/client.ts` defines one `request<T>` helper that parses structured errors and uses `VITE_API_BASE_URL || '/api'`.

- [ ] **Step 5: Verify test/build and commit**

Run: `pnpm --dir web test && pnpm --dir web build`

Expected: PASS and `web/dist` generated without TypeScript errors。

```powershell
git add web
git commit -m "feat: scaffold enterprise assessment frontend"
```

---

### Task 8: New assessment and analyzing pages

**Files:**
- Create: `web/src/features/assessments/NewAssessmentPage.tsx`
- Create: `web/src/features/assessments/AnalyzingPage.tsx`
- Create: `web/src/features/assessments/assessment.css`
- Test: `web/src/features/assessments/NewAssessmentPage.test.tsx`
- Test: `web/src/features/assessments/AnalyzingPage.test.tsx`

**Interfaces:**
- Consumes: `api.createAssessment(FormData)` and `api.getAssessment(id)`。
- Produces: real multipart submission and polling to PLAN_REVIEW/FAILED。

- [ ] **Step 1: 写失败 tests for primary hierarchy and submission**

```tsx
test('keeps demo as a text link and submits real materials', async () => {
  const user = userEvent.setup()
  renderPage()
  expect(screen.getByRole('button', { name: '创建评估' })).toBeVisible()
  expect(screen.getByRole('link', { name: /查看已完成的演示评估/ })).toHaveAttribute('href', '/demo/assessment')
  await user.type(screen.getByLabelText('岗位描述 JD'), '负责 Agent Workflow')
  await user.type(screen.getByLabelText('粘贴简历文本'), '候选人有 LangGraph 项目')
  await user.click(screen.getByRole('button', { name: '创建评估' }))
  expect(api.createAssessment).toHaveBeenCalledTimes(1)
})
```

Analyzing test uses fake timers and verifies polling navigates only when status becomes PLAN_REVIEW; FAILED renders retry without clearing ID.

- [ ] **Step 2: Confirm RED, implement approved layout, then GREEN**

Run: `pnpm --dir web test -- NewAssessmentPage AnalyzingPage`

Expected RED first, then PASS after implementation。

Implementation requirements:

- only AI Agent / AI 应用工程师 is selectable;
- JD sample is inserted by a secondary text action and remains editable;
- file accept is `.pdf,.docx,.txt`;
- file and pasted text are mutually exclusive;
- client validates presence and 5 MiB before network call;
- progress lists actual backend stage labels, not fake percentages;
- loading and error UI uses semantic `aria-live` regions;
- design matches the approved enterprise entry screen.

- [ ] **Step 3: Verify build and commit**

Run: `pnpm --dir web test && pnpm --dir web build`

Expected: PASS。

```powershell
git add web/src/features/assessments web/src/api web/src/app
git commit -m "feat: add enterprise assessment entry"
```

---

### Task 9: Editable plan review page

**Files:**
- Create: `web/src/features/plans/PlanReviewPage.tsx`
- Create: `web/src/features/plans/plan-review.css`
- Test: `web/src/features/plans/PlanReviewPage.test.tsx`

**Interfaces:**
- Consumes: plan endpoint and typed `PlanOverrideSet`。
- Produces: freeze response containing one-time candidate URL。

- [ ] **Step 1: Write failing guardrail UI tests**

```tsx
test('locks core targets but allows business focus edits', async () => {
  renderPlan({ mustCover: true, priority: 'high' })
  expect(screen.getByText('岗位核心 · 不可删除')).toBeVisible()
  expect(screen.queryByRole('button', { name: '删除目标' })).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '业务关注点' })).toBeEnabled()
})

test('freezes plan and shows candidate link only after server success', async () => {
  renderPlan()
  await userEvent.click(screen.getByRole('button', { name: '校验并冻结计划' }))
  expect(await screen.findByLabelText('候选人链接')).toHaveValue(expect.stringContaining('/interviews/'))
})
```

- [ ] **Step 2: Confirm RED, implement, verify GREEN**

Run: `pnpm --dir web test -- PlanReviewPage`

Expected: RED before implementation and PASS after。

Implementation requirements:

- show candidate profile, claims, planned dimensions, target objectives, modes and budget;
- core/Gating targets display locked label and no delete action;
- editable values are priority where allowed, business focus, 30/45/60 minutes and transfer count;
- server 422 guardrail errors display beside the affected section;
- freeze button says `校验并冻结计划` and is disabled while saving;
- successful freeze reveals a copyable one-time candidate URL;
- do not show or generate concrete questions.

- [ ] **Step 3: Verify all frontend tests/build and commit**

Run: `pnpm --dir web test && pnpm --dir web build`

Expected: PASS。

```powershell
git add web/src/features/plans web/src/api
git commit -m "feat: add guarded plan review UI"
```

---

### Task 10: Candidate dynamic interview page

**Files:**
- Create: `web/src/features/interview/InterviewPage.tsx`
- Create: `web/src/features/interview/interview.css`
- Test: `web/src/features/interview/InterviewPage.test.tsx`

**Interfaces:**
- Consumes: get/start/answer endpoints。
- Persists: unsent draft in `sessionStorage` keyed by candidate token + turn ID。
- Never consumes: report/evidence/score fields。

- [ ] **Step 1: Write failing privacy, start and idempotency tests**

```tsx
test('does not start on page load and hides internal assessment data', async () => {
  renderInterview({ state: 'ready', targetRole: 'AI 应用工程师' })
  expect(api.startInterview).not.toHaveBeenCalled()
  expect(screen.queryByText(/Evidence|Rubric|岗位匹配度/)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '开始面试' }))
  expect(api.startInterview).toHaveBeenCalledTimes(1)
})

test('keeps draft after network failure and reuses idempotency key', async () => {
  renderWaitingInterview()
  await userEvent.type(screen.getByLabelText('你的回答'), '使用幂等键和 checkpoint')
  await userEvent.click(screen.getByRole('button', { name: '提交回答' }))
  expect(await screen.findByText('提交失败，回答草稿已保留')).toBeVisible()
  expect(screen.getByLabelText('你的回答')).toHaveValue('使用幂等键和 checkpoint')
})
```

- [ ] **Step 2: Confirm RED, implement candidate experience, verify GREEN**

Run: `pnpm --dir web test -- InterviewPage`

Expected: PASS after implementation。

Implementation requirements:

- ready screen requires explicit start click;
- waiting screen shows target role, elapsed time, public phase, question and prior Q&A;
- no fixed total question count;
- generate one UUID idempotency key per draft and retain it across retry;
- after success, clear only the submitted turn's draft;
- 409 stale-turn response replaces the UI with server current turn and preserves old text separately;
- complete state shows only completion confirmation, not enterprise report;
- use `aria-live="polite"` for generation and save status.

- [ ] **Step 3: Verify and commit**

Run: `pnpm --dir web test && pnpm --dir web build`

Expected: PASS。

```powershell
git add web/src/features/interview web/src/api
git commit -m "feat: add candidate dynamic interview UI"
```

---

### Task 11: Enterprise report and demo report UI

**Files:**
- Create: `web/src/features/report/ReportPage.tsx`
- Create: `web/src/features/report/DemoReportPage.tsx`
- Create: `web/src/features/report/RadarChart.tsx`
- Create: `web/src/features/report/EvidenceDrawer.tsx`
- Create: `web/src/features/report/report.css`
- Test: `web/src/features/report/ReportPage.test.tsx`
- Test: `web/src/features/report/RadarChart.test.tsx`

**Interfaces:**
- Consumes: `ReportViewModel` only。
- Produces: dynamic radar SVG, evidence drawer, path timeline and limitations。

- [ ] **Step 1: Write failing dynamic radar and evidence trace tests**

```tsx
test('renders dimensions from data and never converts unverified to zero', () => {
  render(<RadarChart dimensions={[
    { dimension_id: 'custom_a', name: 'Agent 编排', score: 86, level: 'L3', coverage: .8, confidence: 'high' },
    { dimension_id: 'custom_b', name: '待核验能力', score: null, level: 'UNVERIFIED', coverage: 0, confidence: 'low' },
  ]} />)
  expect(screen.getByText('Agent 编排')).toBeVisible()
  expect(screen.getByText('待核验能力')).toBeVisible()
  expect(screen.getByText('未验证')).toBeVisible()
  expect(screen.queryByText(/^0$/)).not.toBeInTheDocument()
})

test('opens original question and answer for a score reason', async () => {
  renderReport()
  await userEvent.click(screen.getByRole('button', { name: /查看证据 E003/ }))
  expect(screen.getByText(/你如何设计 State/)).toBeVisible()
  expect(screen.getByText(/节点只返回增量更新/)).toBeVisible()
})
```

- [ ] **Step 2: Confirm RED and implement custom SVG radar**

Run: `pnpm --dir web test -- RadarChart ReportPage`

Expected: RED then GREEN。

Radar requirements:

- number of axes equals verified + unverified dimensions from API;
- labels come from API, never a six-label constant;
- unverified points are omitted from the score polygon and shown with dashed axis treatment;
- companion table always displays score/level, coverage and confidence;
- keyboard users can focus each dimension and open detail.

- [ ] **Step 3: Implement data-audit report composition**

Include hero fit dial, metrics strip, radar + bars, dynamic path, score reasons, claim verification, development actions and limitations. Demo page wraps the same `ReportPage` with a persistent `演示数据 · 只读` banner; it does not duplicate report markup.

- [ ] **Step 4: Run frontend suite/build and commit**

Run: `pnpm --dir web test && pnpm --dir web build`

Expected: PASS。

```powershell
git add web/src/features/report web/src/api
git commit -m "feat: render evidence-driven assessment reports"
```

---

### Task 12: Zero-API browser E2E、startup commands and final verification

**Files:**
- Create: `run_web.py`
- Create: `web/playwright.config.ts`
- Create: `web/e2e/demo-report.spec.ts`
- Create: `web/e2e/assessment-flow.spec.ts`
- Modify: `README.md`
- Modify: `.env.example`
- Test: `tests/test_run_web.py`

**Interfaces:**
- Produces: one backend command and one frontend dev command。
- Produces: deterministic E2E mode using injected fake analysis/interview services。

- [ ] **Step 1: Write failing startup smoke test**

```python
# tests/test_run_web.py
import unittest
from unittest.mock import patch
import run_web


class RunWebTest(unittest.TestCase):
    @patch("run_web.uvicorn.run")
    def test_main_starts_app_factory(self, run) -> None:
        self.assertEqual(run_web.main([]), 0)
        run.assert_called_once_with(
            "profile_agent.web.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=8000,
            reload=False,
        )
```

- [ ] **Step 2: Confirm RED and implement startup command**

```python
# run_web.py
import argparse
import uvicorn


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    uvicorn.run(
        "profile_agent.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0
```

- [ ] **Step 3: Add Playwright zero-API tests**

`demo-report.spec.ts` must start with an environment that has no provider key and assert:

```ts
await page.goto('/assessments/new')
await page.getByRole('link', { name: /查看已完成的演示评估/ }).click()
await expect(page.getByText('演示数据 · 只读')).toBeVisible()
await expect(page.getByText('动态追问路径')).toBeVisible()
await page.getByRole('button', { name: /查看证据/ }).first().click()
await expect(page.getByText(/候选人原始回答/)).toBeVisible()
```

`assessment-flow.spec.ts` uses `WEB_FAKE_SERVICES=1` and verifies create → plan review → freeze → candidate start → answers → enterprise report. The fake mode lives only in dependency construction and calls the same routers/services/repository; production components must not contain `if fake` branches.

- [ ] **Step 4: Update README with exact Windows commands**

```powershell
uv sync
pnpm --dir web install

# Terminal 1
.\.venv\Scripts\python.exe run_web.py

# Terminal 2
pnpm --dir web dev

# Open
http://localhost:5173/assessments/new
```

Document that real creation/interview calls the configured provider, while `/demo/assessment` and all automated tests are zero API. Document RapidOCR model installation check and local SQLite files under `data/`.

- [ ] **Step 5: Run focused and full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run_offline_calibration.py --case ALL
.\.venv\Scripts\python.exe -m compileall -q profile_agent tests run_web.py
pnpm --dir web test
pnpm --dir web build
pnpm --dir web e2e
git diff --check
git status --short
```

Expected:

- all Python tests pass;
- C01/C03/C06 all PASS;
- compileall exits 0;
- all Vitest and Playwright tests pass;
- Vite build exits 0;
- diff check is clean;
- only intended files are modified.

- [ ] **Step 6: Run browser visual QA at 1440×900 and 390×844**

Check `/assessments/new`, `/assessments/:id/plan`, `/interviews/:token`, `/demo/assessment` for overflow, focus visibility, keyboard access, loading states, evidence drawer, unverified presentation and visual consistency with the approved data-audit direction. For every behavior or accessibility defect, add a failing component/E2E assertion before the fix; for CSS-only defects, save before/after screenshots in the task notes. Then repeat Step 5.

- [ ] **Step 7: Commit final integration**

```powershell
git add run_web.py README.md .env.example web profile_agent tests pyproject.toml uv.lock .gitignore
git commit -m "feat: deliver enterprise assessment web demo"
```

## Implementation Order and Checkpoints

1. Tasks 1–3 establish backend contracts, ingestion and the correct freeze boundary.
2. Tasks 4–6 expose a complete API and zero-API report before frontend work depends on it.
3. Tasks 7–11 implement the approved UI screen-by-screen with component tests.
4. Task 12 closes the full browser path and startup documentation.

After Tasks 1–6, run a checkpoint review of OpenAPI payloads before building page components. After Task 11, run a visual review before final E2E. Do not run a real provider calibration as part of either checkpoint.

"""Deterministic intent building and safe question retrieval.

The Supervisor decides which requirement to ask about.  This module turns
that decision into a small, reproducible retrieval intent and ranks only the
already-filtered records returned by the disposable question store.  No LLM,
network call, or free-form Supervisor query is used here.

The canonical public result is :class:`QuestionRetrievalResult` from Task 1.
The per-candidate score decomposition is kept on ``QuestionRetriever`` (and
attached as a non-serialised diagnostic attribute on the result) so callers
can audit ranking without widening the strict Task 1 wire contract.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
import math
import re
from typing import Any, Protocol

from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    InterviewPlan,
    QuestionMode,
)
from profile_agent.schemas.job_schema import JobProfile
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.schemas.runtime_schema import InterviewTurn


MAX_QUERY_CHARS = 512
MAX_CANDIDATES = 3
_ROLE = "ai_agent_engineer"

# The vector score remains the main signal, while the other components are
# deliberately bounded tie-breakers.  Keeping these constants explicit makes
# the selection rule explainable and easy to calibrate without changing the
# surrounding contracts.
VECTOR_WEIGHT = 0.72
TRUST_WEIGHT = 0.08
FRESHNESS_WEIGHT = 0.08
COVERAGE_WEIGHT = 0.08
MODE_WEIGHT = 0.04
DUPLICATE_PENALTY_WEIGHT = 0.06
ASKED_PENALTY_WEIGHT = 0.04

_MODE_DIFFICULTY: dict[QuestionMode, str] = {
    "foundation": "foundation",
    "project_deep_dive": "intermediate",
    "scenario": "intermediate",
    "system_design": "advanced",
    "coding": "intermediate",
    "follow_up": "intermediate",
}
_TRUST_SCORE = {"high": 1.0, "medium": 0.66, "low": 0.33}
_EMAIL_RE = re.compile(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
# Treat an anchor as untrusted text.  The assignment/header pattern is
# deliberately not anchored with ``\b``: environment variables such as
# ``SILICONFLOW_API_KEY`` have an underscore immediately before ``API_KEY``.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:[a-z0-9][a-z0-9_.-]*[_.-])?"
    r"(?:api[_-]?(?:key|secret|token)|access[_-]?(?:key|secret|token)|"
    r"private[_-]?(?:key|secret|token)|client[_-]?(?:secret|token|key)|"
    r"auth(?:entication)?[_-]?(?:token|key)|secret|token|authorization|"
    r"bearer|password|credential)"
    r"(?:[a-z0-9_.-]*)\s*(?:=|:|\s+)\s*[^\s,;|]+"
)
_AUTH_HEADER_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:authorization|proxy-authorization|x-api-key|api-key)"
    r"\s*[:=]\s*(?:(?:bearer|basic|token|jwt)\s+)?[^\s,;|]+"
)
_AUTH_HEADER_SPACED_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:authorization|proxy-authorization|x-api-key|api-key)"
    r"\s+(?:(?:bearer|basic|token|jwt)\s+)?[^\s,;|]+"
)
_AUTH_SCHEME_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:bearer|basic|token|jwt)\s+"
    r"[a-z0-9._~+/=-]+"
)
_SECRET_LABEL_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:api[_-]?(?:key|secret|token)|"
    r"access[_-]?(?:key|secret|token)|private[_-]?(?:key|secret|token)|"
    r"client[_-]?(?:secret|token|key)|auth(?:entication)?[_-]?(?:token|key)|"
    r"secret(?:[_-]?token)?|token|authorization|bearer|password|credential)"
    r"(?![a-z0-9])"
)
_SECRET_PREFIX_RE = re.compile(
    r"(?ix)(?<![a-z0-9])(?:(?:sk|rk|pk)[_-](?:(?:live|test|proj)[_-])?"
    r"[a-z0-9_-]{4,}|"
    r"(?:gh[pousr]_|github_pat_)[a-z0-9_]{8,}|"
    r"(?:akia|asia)[a-z0-9]{12,}|AIza[a-z0-9_-]{20,}|"
    r"xox[baprs]-[a-z0-9-]{8,})"
)
_JWT_RE = re.compile(
    r"(?ix)(?<![a-z0-9])eyj[a-z0-9_-]{6,}\.[a-z0-9_-]{4,}\.[a-z0-9_-]{4,}"
)
_LONG_OPAQUE_RE = re.compile(
    r"(?ix)(?<![a-z0-9])"
    r"(?=[a-z0-9._-]{24,}(?![a-z0-9]))"
    r"(?=[a-z0-9._-]*[a-z])(?=[a-z0-9._-]*\d)"
    r"[a-z0-9._-]{24,}(?![a-z0-9])"
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{6,}\d)(?!\w)")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]")
_SENSITIVE_MARKERS = (
    "expected_signals",
    "critical_errors",
    "expected signal",
    "critical error",
    "标准答案",
    "评分标准",
    "rubric",
)
_REDACTED_SECRET = "[redacted]"

_QUERY_SECTION_BUDGETS = {
    "dimension": 32,
    "mode": 32,
    "depth": 32,
    "objective": 68,
    "requirement": 80,
    "coverage_gap": 60,
    "jd": 60,
    "resume": 60,
    "recent": 60,
}


class EmbeddingClient(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed the supplied texts in input order."""


class RetrievalScoreBreakdown:
    """Immutable-ish diagnostic representation for one ranked candidate."""

    __slots__ = (
        "question_id",
        "source_id",
        "index_version",
        "rank",
        "selected",
        "vector_similarity",
        "trust",
        "freshness",
        "coverage",
        "mode",
        "duplicate_penalty",
        "asked_penalty",
        "total_score",
    )

    def __init__(
        self,
        *,
        question_id: str,
        source_id: str,
        index_version: str,
        rank: int,
        selected: bool,
        vector_similarity: float,
        trust: float,
        freshness: float,
        coverage: float,
        mode: float,
        duplicate_penalty: float,
        asked_penalty: float,
        total_score: float,
    ) -> None:
        self.question_id = question_id
        self.source_id = source_id
        self.index_version = index_version
        self.rank = rank
        self.selected = selected
        self.vector_similarity = vector_similarity
        self.trust = trust
        self.freshness = freshness
        self.coverage = coverage
        self.mode = mode
        self.duplicate_penalty = duplicate_penalty
        self.asked_penalty = asked_penalty
        self.total_score = total_score

    @property
    def score(self) -> float:
        return self.total_score

    @property
    def components(self) -> dict[str, float]:
        return {
            "vector_similarity": self.vector_similarity,
            "trust": self.trust,
            "freshness": self.freshness,
            "coverage": self.coverage,
            "mode": self.mode,
            "duplicate_penalty": self.duplicate_penalty,
            "asked_penalty": self.asked_penalty,
            "total_score": self.total_score,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "source_id": self.source_id,
            "index_version": self.index_version,
            "rank": self.rank,
            "selected": self.selected,
            "score": self.total_score,
            "components": dict(self.components),
        }


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    """Normalize text and redact common secrets/PII before it enters a query."""

    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    # Redact headers/assignments before generic schemes and key prefixes.  The
    # replacements are stable and intentionally carry no source text.
    text = _AUTH_HEADER_RE.sub(_REDACTED_SECRET, text)
    text = _AUTH_HEADER_SPACED_RE.sub(_REDACTED_SECRET, text)
    text = _SECRET_ASSIGNMENT_RE.sub(_REDACTED_SECRET, text)
    text = _AUTH_SCHEME_RE.sub(_REDACTED_SECRET, text)
    text = _SECRET_PREFIX_RE.sub(_REDACTED_SECRET, text)
    text = _JWT_RE.sub(_REDACTED_SECRET, text)
    text = _LONG_OPAQUE_RE.sub(_REDACTED_SECRET, text)
    text = _SECRET_LABEL_RE.sub(_REDACTED_SECRET, text)
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _URL_RE.sub("[redacted-url]", text)
    text = _PHONE_RE.sub("[redacted-phone]", text)
    if limit is not None:
        if len(text) > limit and limit > 1:
            tail_budget = max(1, limit // 3)
            head_budget = limit - tail_budget - 1
            text = (
                text[:head_budget].rstrip()
                + "…"
                + text[-tail_budget:].lstrip()
            )
        else:
            text = text[:limit].rstrip()
    return text


def _is_sensitive_text(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _dedupe_non_blank(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in values:
        value = _clean_text(raw)
        if not value or value in seen:
            continue
        if _is_sensitive_text(value):
            continue
        seen.add(value)
        cleaned.append(value)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _bounded_section(label: str, value: str, budget: int) -> str:
    """Keep a labelled anchor within its own budget.

    Section budgets are applied before joining the query.  This prevents a
    long objective or answer from starving later JD/resume/recent anchors.
    """

    prefix = f"{label}="
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    value_budget = max(0, budget - len(prefix))
    if len(cleaned) <= value_budget:
        return prefix + cleaned
    if value_budget <= 1:
        return prefix + cleaned[:value_budget]

    # Preserve both the beginning (usually the structured field name) and the
    # tail (often the discriminating project/answer detail) within the field's
    # own budget.  The ellipsis is deterministic and does not trigger another
    # global truncation pass.
    tail_budget = max(1, value_budget // 3)
    head_budget = value_budget - tail_budget - 1
    head = cleaned[:head_budget].rstrip()
    tail = cleaned[-tail_budget:].lstrip()
    return prefix + head + "…" + tail


def _clip_query(parts: Sequence[str]) -> str:
    query = " | ".join(part for part in parts if part)
    if len(query) > MAX_QUERY_CHARS:
        # All normal inputs are bounded by _QUERY_SECTION_BUDGETS.  Raising
        # here protects the invariant from future additions instead of
        # silently dropping an entire trailing section.
        raise ValueError("retrieval intent query exceeds its length budget")
    return query.rstrip()


def _resume_anchors(profile: ResumeProfile | None) -> list[str]:
    if profile is None:
        return []

    anchors: list[str] = []
    skills = _dedupe_non_blank(profile.skills, limit=6)
    if skills:
        anchors.append("skills=" + ",".join(skills))

    # One compact project anchor is enough to personalize retrieval.  Taking
    # the first project is intentional: the query must not become a copy of a
    # complete resume, and the source model already provides stable ordering.
    if profile.projects:
        project = profile.projects[0]
        name = _clean_text(project.name, limit=48)
        description = _clean_text(project.description, limit=72)
        technologies = ",".join(_dedupe_non_blank(project.technologies, limit=4))
        project_parts = [part for part in (name, description) if part]
        if technologies:
            project_parts.append(technologies)
        if project_parts:
            anchors.append("project=" + "/".join(project_parts))

    return anchors


def _job_anchors(profile: JobProfile | None) -> list[str]:
    if profile is None:
        return []

    anchors: list[str] = []
    for requirement in profile.requirements[:4]:
        name = _clean_text(requirement.name, limit=32)
        description = _clean_text(requirement.description, limit=72)
        value = ":".join(part for part in (name, description) if part)
        if value and not _is_sensitive_text(value):
            anchors.append(value)
    if not anchors:
        anchors.extend(_dedupe_non_blank(profile.responsibilities, limit=3))
    return anchors


def _recent_answer_anchors(turns: Sequence[InterviewTurn]) -> list[str]:
    answered = [turn for turn in turns if (turn.answer or "").strip()]
    answered.sort(key=lambda turn: (turn.sequence_number, turn.id))
    anchors: list[str] = []
    for turn in answered[-2:]:
        question = _clean_text(turn.question, limit=72)
        answer = _clean_text(turn.answer, limit=112)
        value = ":".join(part for part in (question, answer) if part)
        if value and not _is_sensitive_text(value):
            anchors.append(value)
    return anchors


def build_question_retrieval_intent(
    *,
    action: AskAction,
    plan: InterviewPlan,
    resume_profile: ResumeProfile | None = None,
    job_profile: JobProfile | None = None,
    recent_turns: Sequence[InterviewTurn] = (),
    evidence_summaries: Sequence[str] = (),
    excluded_question_ids: Sequence[str] = (),
) -> QuestionRetrievalIntent:
    """Build a stable, bounded retrieval intent from resolved runtime facts.

    The lookup dimension comes from the exact requirement selected by
    Supervisor.  Guessing a dimension when a plan omitted it would silently
    retrieve the wrong competency, so legacy/incomplete plans fail explicitly.
    """

    if not isinstance(action, AskAction):
        raise TypeError("action must be AskAction")
    if not isinstance(plan, InterviewPlan):
        raise TypeError("plan must be InterviewPlan")

    matching_targets = [
        candidate for candidate in plan.targets if candidate.id == action.target_id
    ]
    if len(matching_targets) > 1:
        raise ValueError(f"duplicate target_id: {action.target_id}")
    target: AssessmentTarget | None = matching_targets[0] if matching_targets else None
    if target is None:
        raise ValueError(f"unknown target_id: {action.target_id}")

    matching_requirements = [
        candidate
        for candidate in target.evidence_requirements
        if candidate.id == action.primary_requirement_id
    ]
    if len(matching_requirements) > 1:
        raise ValueError(
            f"duplicate requirement_id: {action.primary_requirement_id}"
        )
    requirement = matching_requirements[0] if matching_requirements else None
    if requirement is None:
        raise ValueError(
            "primary_requirement_id does not belong to target_id: "
            f"{action.primary_requirement_id}"
        )

    dimension_id = (requirement.planned_role_dimension_id or "").strip()
    if not dimension_id:
        raise ValueError(
            f"requirement {requirement.id} is missing planned dimension_id"
        )

    difficulty = _MODE_DIFFICULTY[action.question_mode]
    exclusions = sorted(
        {
            cleaned
            for cleaned in (_clean_text(value) for value in excluded_question_ids)
            if cleaned
        }
    )

    # Keep sections explicit so future audits can explain exactly which
    # bounded fact contributed to a query.  Do not include action.reason: it
    # is free-form and may contain candidate or provider data.
    evidence = _dedupe_non_blank(evidence_summaries, limit=3)
    parts = [
        _bounded_section(
            "dimension",
            dimension_id,
            _QUERY_SECTION_BUDGETS["dimension"],
        ),
        _bounded_section(
            "mode",
            action.question_mode,
            _QUERY_SECTION_BUDGETS["mode"],
        ),
        _bounded_section("depth", difficulty, _QUERY_SECTION_BUDGETS["depth"]),
        _bounded_section(
            "objective",
            target.objective,
            _QUERY_SECTION_BUDGETS["objective"],
        ),
        _bounded_section(
            "requirement",
            requirement.description,
            _QUERY_SECTION_BUDGETS["requirement"],
        ),
    ]
    if evidence:
        parts.append(
            _bounded_section(
                "coverage_gap",
                ";".join(evidence),
                _QUERY_SECTION_BUDGETS["coverage_gap"],
            )
        )

    job = _job_anchors(job_profile)
    if job:
        parts.append(
            _bounded_section("jd", ";".join(job), _QUERY_SECTION_BUDGETS["jd"])
        )

    resume = _resume_anchors(resume_profile)
    if resume:
        parts.append(
            _bounded_section(
                "resume",
                ";".join(resume),
                _QUERY_SECTION_BUDGETS["resume"],
            )
        )

    recent = _recent_answer_anchors(recent_turns)
    if recent:
        parts.append(
            _bounded_section(
                "recent",
                ";".join(recent),
                _QUERY_SECTION_BUDGETS["recent"],
            )
        )

    query_text = _clip_query(parts)
    # The static fields above should always leave a non-empty query.  Keep an
    # explicit guard because QuestionRetrievalIntent has a strict contract.
    if not query_text:
        raise ValueError("retrieval intent query_text must not be blank")

    return QuestionRetrievalIntent(
        query_text=query_text,
        role=_ROLE,
        dimension_id=dimension_id,
        question_mode=action.question_mode,
        difficulty=difficulty,
        excluded_question_ids=exclusions,
    )


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(value) if token.strip()}


def _normalised_question_text(record: InterviewQuestionRecord) -> str:
    return re.sub(r"\s+", " ", record.question_text).strip().lower()


def _coverage_score(intent: QuestionRetrievalIntent, record: InterviewQuestionRecord) -> float:
    query_tokens = _tokens(intent.query_text)
    candidate_tokens = _tokens(" ".join([record.question_text, *record.skills]))
    if not query_tokens or not candidate_tokens:
        return 0.0
    return round(len(query_tokens & candidate_tokens) / len(query_tokens), 6)


def _freshness_score(record: InterviewQuestionRecord, today: date) -> float:
    age_days = max(0, (today - record.verified_at).days)
    # One year is a bounded freshness horizon.  Validity is hard-filtered by
    # the store and again by the service, so this component is only a gentle
    # preference among otherwise current records.
    return round(max(0.0, min(1.0, 1.0 - age_days / 365.0)), 6)


def _finite_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(score):
        return None
    return score


def _vector_score(value: Any) -> float:
    score = _finite_score(value)
    if score is None:
        raise ValueError("retrieved question score must be finite")
    return round(max(-1.0, min(1.0, score)), 6)


def _coerce_hit(value: Any, *, index_version: str | None) -> RetrievedQuestion | None:
    # The store boundary must return the Task 1 hit model.  Accepting a bare
    # record (or reconstructing one from arbitrary mappings) would make a
    # non-vector source look like a retrieval hit.
    if not isinstance(value, RetrievedQuestion):
        return None
    if not isinstance(value.record, InterviewQuestionRecord):
        return None
    if _finite_score(value.score) is None:
        return None
    if not isinstance(value.index_version, str) or not value.index_version.strip():
        return None
    if not isinstance(value.record.source_id, str) or not value.record.source_id.strip():
        return None
    return value


def _store_status_and_hits(value: Any) -> tuple[str, list[Any], str | None]:
    if value is None:
        return "unavailable", [], None
    status = getattr(value, "status", None)
    hits = getattr(value, "hits", None)
    if hits is None:
        hits = getattr(value, "results", None)
    index_version = getattr(value, "index_version", None)
    if not isinstance(status, str):
        return "unavailable", [], None
    if hits is None:
        hits = []
    try:
        hit_list = list(hits)
    except TypeError:
        return "unavailable", [], index_version if isinstance(index_version, str) else None
    return status, hit_list, index_version if isinstance(index_version, str) else None


class QuestionRetriever:
    """Embed one intent, apply safe store results, and select one question."""

    def __init__(
        self,
        embedding_client: EmbeddingClient | Any | None = None,
        store: Any | None = None,
        *,
        embedding: EmbeddingClient | Any | None = None,
        question_store: Any | None = None,
        today: date | None = None,
        as_of: date | None = None,
        max_candidates: int = MAX_CANDIDATES,
    ) -> None:
        if embedding_client is not None and embedding is not None:
            raise ValueError("pass only one of embedding_client and embedding")
        if store is not None and question_store is not None:
            raise ValueError("pass only one of store and question_store")
        if today is not None and as_of is not None:
            raise ValueError("pass only one of today and as_of")
        configured_date = today if today is not None else as_of
        if configured_date is not None and (
            isinstance(configured_date, datetime)
            or not isinstance(configured_date, date)
        ):
            raise TypeError("today must be a date")
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
            raise ValueError("max_candidates must be a positive integer")
        self.embedding_client = (
            embedding_client if embedding_client is not None else embedding
        )
        self.store = store if store is not None else question_store
        self.today = today if today is not None else as_of
        self.max_candidates = min(MAX_CANDIDATES, max_candidates)
        self.last_rank_trace: list[dict[str, Any]] = []
        # Compatibility aliases for diagnostics used by callers that name the
        # same internal audit data differently.
        self.last_ranking_trace = self.last_rank_trace
        self.last_trace = self.last_rank_trace

    def retrieve(
        self,
        intent: QuestionRetrievalIntent,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> QuestionRetrievalResult:
        if not isinstance(intent, QuestionRetrievalIntent):
            raise TypeError("intent must be QuestionRetrievalIntent")
        as_of = today if today is not None else (self.today or date.today())
        if isinstance(as_of, datetime) or not isinstance(as_of, date):
            raise TypeError("today must be a date")
        self.last_rank_trace.clear()

        if self.embedding_client is None or self.store is None:
            return self._result("unavailable", as_of=as_of)

        try:
            query_vector = self._embed(intent.query_text)
        except Exception:
            return self._result("unavailable", as_of=as_of)

        requested_limit = self.max_candidates if limit is None else limit
        if isinstance(requested_limit, bool) or not isinstance(requested_limit, int) or requested_limit < 1:
            raise ValueError("limit must be a positive integer")
        requested_limit = min(MAX_CANDIDATES, requested_limit)

        try:
            raw_store_result = self.store.search(
                intent=intent,
                query_vector=query_vector,
                today=as_of,
                limit=requested_limit,
            )
        except Exception:
            return self._result("unavailable", as_of=as_of)

        status, raw_hits, store_index_version = _store_status_and_hits(raw_store_result)
        if status in {"unavailable", "index_mismatch", "no_match"}:
            return self._result(status, as_of=as_of)
        if status != "hit":
            return self._result("unavailable", as_of=as_of)

        candidates: list[RetrievedQuestion] = []
        malformed_hit = False
        excluded_ids = set(intent.excluded_question_ids)
        for raw_hit in raw_hits:
            hit = _coerce_hit(raw_hit, index_version=store_index_version)
            if hit is None:
                malformed_hit = True
                continue
            if (
                store_index_version is not None
                and hit.index_version != store_index_version
            ):
                malformed_hit = True
                continue
            record = hit.record
            # Store filters are authoritative for efficiency, but this second
            # boundary keeps an injected/faulty store from bypassing lifecycle,
            # role, dimension, mode, or asked-question rules.
            if record.role != intent.role:
                continue
            if record.dimension_id != intent.dimension_id:
                continue
            if record.question_mode != intent.question_mode:
                continue
            if record.status != "active" or record.valid_until < as_of:
                continue
            if record.question_id in excluded_ids:
                continue
            candidates.append(hit)

        if not candidates:
            if malformed_hit:
                return self._result("unavailable", as_of=as_of)
            return self._result("no_match", as_of=as_of)

        ranked = self._rank(
            candidates,
            intent=intent,
            today=as_of,
            limit=requested_limit,
        )
        selected, selected_breakdown = ranked[0]
        selected_question = selected.model_copy(update={"score": selected_breakdown.total_score})
        index_version = selected_question.index_version or store_index_version
        if index_version is not None:
            selected_question = selected_question.model_copy(
                update={"index_version": index_version}
            )

        result = QuestionRetrievalResult(
            status="hit",
            as_of=as_of,
            selected_question=selected_question,
            trace=QuestionRetrievalTrace(
                status="hit",
                question_id=selected_question.question_id,
                source_id=selected_question.source_id,
                score=selected_question.score,
                index_version=selected_question.index_version,
            ),
        )
        self._attach_trace(result)
        return result

    def _embed(self, query_text: str) -> list[float]:
        embed = getattr(self.embedding_client, "embed", None)
        if callable(embed):
            raw = embed([query_text])
        elif callable(self.embedding_client):
            raw = self.embedding_client([query_text])
        else:
            raise TypeError("embedding client must provide embed")

        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values = list(raw)
        else:
            raise ValueError("embedding result must be a sequence")
        # Accept a direct one-vector fake for test adapters while preserving
        # the normal EmbeddingClient list-of-vectors contract.
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            values = [values]
        if len(values) != 1:
            raise ValueError("embedding result must contain exactly one vector")
        vector = values[0]
        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)) or not vector:
            raise ValueError("embedding vector must be non-empty")
        normalized: list[float] = []
        for value in vector:
            if isinstance(value, bool):
                raise ValueError("embedding vector must contain finite numbers")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("embedding vector must contain finite numbers")
            normalized.append(numeric)
        return normalized

    def _rank(
        self,
        candidates: Sequence[RetrievedQuestion],
        *,
        intent: QuestionRetrievalIntent,
        today: date,
        limit: int = MAX_CANDIDATES,
    ) -> list[tuple[RetrievedQuestion, RetrievalScoreBreakdown]]:
        by_text: dict[str, list[RetrievedQuestion]] = defaultdict(list)
        for candidate in candidates:
            by_text[_normalised_question_text(candidate.record)].append(candidate)
        duplicate_owner: dict[str, str] = {}
        for text, group in by_text.items():
            duplicate_owner[text] = min(item.question_id for item in group)

        scored: list[tuple[RetrievedQuestion, RetrievalScoreBreakdown]] = []
        excluded = set(intent.excluded_question_ids)
        for candidate in candidates:
            record = candidate.record
            vector_similarity = _vector_score(candidate.score)
            trust = _TRUST_SCORE.get(record.trust_level, 0.0)
            freshness = _freshness_score(record, today)
            coverage = _coverage_score(intent, record)
            mode = 1.0 if record.question_mode == intent.question_mode else 0.0
            text = _normalised_question_text(record)
            duplicate_penalty = float(
                bool(text and duplicate_owner.get(text) != record.question_id)
            )
            asked_penalty = float(record.question_id in excluded)
            total = (
                VECTOR_WEIGHT * max(0.0, vector_similarity)
                + TRUST_WEIGHT * trust
                + FRESHNESS_WEIGHT * freshness
                + COVERAGE_WEIGHT * coverage
                + MODE_WEIGHT * mode
                - DUPLICATE_PENALTY_WEIGHT * duplicate_penalty
                - ASKED_PENALTY_WEIGHT * asked_penalty
            )
            breakdown = RetrievalScoreBreakdown(
                question_id=record.question_id,
                source_id=record.source_id,
                index_version=candidate.index_version,
                rank=0,
                selected=False,
                vector_similarity=vector_similarity,
                trust=trust,
                freshness=freshness,
                coverage=coverage,
                mode=mode,
                duplicate_penalty=duplicate_penalty,
                asked_penalty=asked_penalty,
                total_score=round(total, 9),
            )
            scored.append((candidate, breakdown))

        scored.sort(
            key=lambda item: (
                -item[1].total_score,
                -item[1].vector_similarity,
                -item[1].trust,
                -item[1].freshness,
                item[0].question_id,
                item[0].source_id,
            )
        )

        traces: list[dict[str, Any]] = []
        final: list[tuple[RetrievedQuestion, RetrievalScoreBreakdown]] = []
        for index, (candidate, breakdown) in enumerate(scored[:limit], start=1):
            breakdown.rank = index
            breakdown.selected = index == 1
            final.append((candidate, breakdown))
            traces.append(breakdown.as_dict())
        self.last_rank_trace.extend(traces)
        return final

    def _attach_trace(self, result: QuestionRetrievalResult) -> None:
        # Task 1 intentionally forbids extra serialized fields.  ``object``
        # assignment gives local diagnostics access without changing that
        # contract or leaking scores to the public report layer.
        object.__setattr__(result, "rank_trace", [dict(item) for item in self.last_rank_trace])
        object.__setattr__(result, "ranking_trace", result.rank_trace)

    @staticmethod
    def _result(status: str, *, as_of: date) -> QuestionRetrievalResult:
        return QuestionRetrievalResult(status=status, as_of=as_of)  # type: ignore[arg-type]


__all__ = [
    "ASKED_PENALTY_WEIGHT",
    "COVERAGE_WEIGHT",
    "DUPLICATE_PENALTY_WEIGHT",
    "EmbeddingClient",
    "FRESHNESS_WEIGHT",
    "MAX_QUERY_CHARS",
    "MODE_WEIGHT",
    "QuestionRetriever",
    "RetrievalScoreBreakdown",
    "TRUST_WEIGHT",
    "VECTOR_WEIGHT",
    "build_question_retrieval_intent",
]

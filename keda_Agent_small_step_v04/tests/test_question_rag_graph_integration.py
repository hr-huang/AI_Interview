from __future__ import annotations

from datetime import date, datetime, timezone
import inspect
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AskAction,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionModePolicy,
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)
from profile_agent.schemas.runtime_schema import AnswerProcessingResult, InterviewTurn
from profile_agent.services.question_retrieval_service import QuestionRetriever
from profile_agent.services.runtime_state_service import initialize_runtime_state
from profile_agent.web.app import create_app
from profile_agent.web.container import (
    LazyQuestionRetriever,
    WebContainer,
    _question_retriever_factory_from_env,
)
from profile_agent.web.interview_service import InterviewService
from tests.report_test_helpers import make_test_report
import run_question_bank


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=4,
        closing_buffer_minutes=0,
        targets=[
            AssessmentTarget(
                id="target_rag",
                objective="验证 Agent 失败恢复能力",
                target_type="problem_solving",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="requirement_rag",
                        description="能够在业务约束下分析失败恢复与取舍",
                        planned_role_dimension_id="role_dim_01",
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["scenario"],
            )
        ],
    )


def make_record() -> InterviewQuestionRecord:
    return InterviewQuestionRecord(
        question_id="q_graph_private",
        question_text="GRAPH_ORIGINAL_QUESTION",
        role="ai_agent_engineer",
        role_version="2026-H2",
        dimension_id="role_dim_01",
        skills=["GRAPH_SKILL"],
        question_mode="scenario",
        difficulty="intermediate",
        expected_signals=["GRAPH_EXPECTED_SIGNAL"],
        critical_errors=["GRAPH_CRITICAL_ERROR"],
        follow_up_seeds=["GRAPH_FOLLOW_UP_SEED"],
        company_tags=[],
        source_id="src_graph_private",
        source_url="https://example.com/graph",
        source_title="private graph source",
        source_type="GRAPH_SOURCE_TYPE",
        published_at=date(2026, 8, 1),
        verified_at=date(2026, 8, 20),
        valid_until=date(2027, 2, 20),
        trust_level="medium",
        status="active",
        version=1,
        content_hash="sha256:graph",
    )


def make_hit_result() -> QuestionRetrievalResult:
    record = make_record()
    selected = RetrievedQuestion(record=record, score=0.9, index_version="idx-graph")
    return QuestionRetrievalResult(
        status="hit",
        as_of=date(2026, 8, 26),
        selected_question=selected,
        trace=QuestionRetrievalTrace(
            status="hit",
            question_id=record.question_id,
            source_id=record.source_id,
            score=0.9,
            index_version="idx-graph",
        ),
    )


class FakeRetriever:
    def __init__(self, result: QuestionRetrievalResult) -> None:
        self.result = result
        self.calls = []

    def retrieve(self, intent):
        self.calls.append(intent)
        return self.result


class FakeQuestionGenerator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        *,
        action: AskAction,
        plan: InterviewPlan,
        claim_registry=None,
        recent_turns=None,
        retrieval_result=None,
    ) -> GeneratedQuestion:
        self.calls.append(
            {
                "action": action,
                "plan": plan,
                "claim_registry": claim_registry,
                "recent_turns": recent_turns,
                "retrieval_result": retrieval_result,
            }
        )
        return GeneratedQuestion(text=f"生成问题 {len(self.calls)}")


class CloseSpy:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def no_op_answer_processor(
    *,
    plan: InterviewPlan,
    runtime_state,
    turn: InterviewTurn,
    existing_evidences,
    claim_registry=None,
) -> AnswerProcessingResult:
    return AnswerProcessingResult(new_evidences=[], runtime_state=runtime_state)


class QuestionRagGraphIntegrationTests(unittest.TestCase):
    NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)

    @staticmethod
    def initial_state() -> dict:
        return {
            "assessment_id": "ast-rag",
            "interview_plan": make_plan(),
            "claim_registry": ClaimRegistry(),
            "runtime_state": initialize_runtime_state(
                make_plan(), started_at=QuestionRagGraphIntegrationTests.NOW
            ),
            "interview_turns": [],
            "evidences": [],
        }

    def test_graph_and_container_accept_keyword_only_policy_manifest_dependencies(self) -> None:
        parameters = inspect.signature(build_interview_graph).parameters
        for name in ("question_mode_policy", "question_bank_manifest"):
            with self.subTest(name=name):
                self.assertEqual(
                    parameters[name].kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )

        container_parameters = inspect.signature(WebContainer).parameters
        for name in ("question_mode_policy", "question_bank_manifest"):
            with self.subTest(container_field=name):
                self.assertEqual(
                    container_parameters[name].kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )

        legacy_container = WebContainer(
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
        )
        self.assertIsNone(legacy_container.question_mode_policy)

        policy = QuestionModePolicy.default()
        graph = build_interview_graph(
            question_mode_policy=policy,
            question_bank_manifest=None,
        )
        self.assertIsNotNone(graph)

        container = WebContainer.for_test(
            repository=object(),
            pre_interview_graph=object(),
            dispatcher=object(),
            question_mode_policy=policy,
            question_bank_manifest=None,
        )
        self.assertIs(container.question_mode_policy, policy)
        self.assertIsNone(container.question_bank_manifest)

        direct_container = WebContainer(
            object(),
            object(),
            object(),
            object(),
            object(),
            question_mode_policy=policy,
            question_bank_manifest=None,
        )
        self.assertIs(direct_container.question_mode_policy, policy)

    @staticmethod
    def config() -> dict:
        return {"configurable": {"thread_id": "rag-graph"}}

    @staticmethod
    def interrupt_payload(result: dict) -> dict:
        interrupts = result.get("__interrupt__", [])
        if not interrupts:
            raise AssertionError(f"expected interrupt, got {result!r}")
        return interrupts[0].value

    def build(self, retriever, generator):
        return build_interview_graph(
            question_generator=generator,
            question_retriever=retriever,
            answer_processor=no_op_answer_processor,
            report_generator=lambda **_: make_test_report(),
            now_provider=lambda: self.NOW,
        )

    def test_graph_retrieves_once_before_generation_and_persists_private_trace(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        result = graph.invoke(self.initial_state(), self.config())
        payload = self.interrupt_payload(result)
        state = graph.get_state(self.config()).values

        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn(("supervisor", "retrieve_question"), edges)
        self.assertIn(("retrieve_question", "generate_question"), edges)
        self.assertIn(("generate_question", "wait_for_answer"), edges)
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(
            generator.calls[0]["retrieval_result"].selected_question.question_id,
            "q_graph_private",
        )
        self.assertEqual(
            generator.calls[0]["retrieval_result"].selected_question.record.primary_mode,
            "scenario",
        )
        self.assertEqual(
            state["interview_turns"][0].retrieval_trace.status,
            "hit",
        )
        self.assertIsNone(state.get("question_retrieval_result"))
        self.assertEqual(payload["question"], "生成问题 1")
        serialized_payload = repr(payload)
        for forbidden in (
            "q_graph_private",
            "src_graph_private",
            "idx-graph",
            "GRAPH_EXPECTED_SIGNAL",
            "GRAPH_CRITICAL_ERROR",
            "GRAPH_FOLLOW_UP_SEED",
        ):
            self.assertNotIn(forbidden, serialized_payload)

    def test_graph_legacy_projection_policy_failure_degrades_to_unavailable(self) -> None:
        base = make_hit_result()
        invalid_record = base.selected_question.record.model_copy(
            update={
                "dimension_id": "role_dim_01",
                "question_mode": "coding",
            }
        )
        invalid_result = QuestionRetrievalResult(
            status="hit",
            as_of=base.as_of,
            selected_question=RetrievedQuestion(
                record=invalid_record,
                score=base.selected_question.score,
                index_version=base.selected_question.index_version,
            ),
            trace=QuestionRetrievalTrace(
                status="hit",
                question_id=invalid_record.question_id,
                source_id=invalid_record.source_id,
                score=base.selected_question.score,
                index_version=base.selected_question.index_version,
            ),
        )
        retriever = FakeRetriever(invalid_result)
        generator = FakeQuestionGenerator()

        graph = self.build(retriever, generator)
        result = graph.invoke(self.initial_state(), self.config())

        self.assertEqual(self.interrupt_payload(result)["question"], "生成问题 1")
        self.assertEqual(
            generator.calls[0]["retrieval_result"].status,
            "unavailable",
        )

    def test_resume_does_not_retrieve_again_for_the_same_interrupt(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)
        config = self.config()

        graph.invoke(self.initial_state(), config)
        graph.invoke(Command(resume="candidate answer"), config)
        self.assertEqual(len(retriever.calls), 2)
        self.assertEqual(len(generator.calls), 2)

    def test_unavailable_retrieval_still_reaches_candidate_interrupt(self) -> None:
        retriever = FakeRetriever(QuestionRetrievalResult(status="unavailable"))
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        result = graph.invoke(self.initial_state(), self.config())
        payload = self.interrupt_payload(result)
        state = graph.get_state(self.config()).values

        self.assertEqual(payload["question"], "生成问题 1")
        self.assertEqual(generator.calls[0]["retrieval_result"].status, "unavailable")
        self.assertEqual(state["interview_turns"][0].retrieval_trace.status, "unavailable")

    def test_public_turn_projection_omits_private_retrieval_provenance(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        graph.invoke(self.initial_state(), self.config())
        state = graph.get_state(self.config()).values

        public_turns = InterviewService._public_turns(state["interview_turns"])
        self.assertEqual(
            public_turns,
            [
                {
                    "id": public_turns[0]["id"],
                    "sequence_number": 1,
                    "question": "生成问题 1",
                    "answer": None,
                }
            ],
        )
        serialized = repr(public_turns)
        for forbidden in (
            "q_graph_private",
            "src_graph_private",
            "idx-graph",
            "GRAPH_EXPECTED_SIGNAL",
            "GRAPH_CRITICAL_ERROR",
            "GRAPH_FOLLOW_UP_SEED",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_test_container_can_inject_the_retriever_without_eager_provider_setup(self) -> None:
        retriever = FakeRetriever(QuestionRetrievalResult(status="unavailable"))

        container = WebContainer.for_test(
            repository=object(),
            pre_interview_graph=object(),
            dispatcher=object(),
            question_retriever=retriever,
        )

        self.assertIs(container.question_retriever, retriever)

    def test_question_retriever_closes_owned_dependencies_once(self) -> None:
        embedding = CloseSpy()
        store = CloseSpy()
        retriever = QuestionRetriever(
            embedding_client=embedding,
            store=store,
            owns_embedding_client=True,
            owns_store=True,
        )

        retriever.close()
        retriever.close()

        self.assertEqual(embedding.close_calls, 1)
        self.assertEqual(store.close_calls, 1)

    def test_question_retriever_does_not_close_external_dependencies(self) -> None:
        embedding = CloseSpy()
        store = CloseSpy()
        retriever = QuestionRetriever(embedding_client=embedding, store=store)

        retriever.close()
        retriever.close()

        self.assertEqual(embedding.close_calls, 0)
        self.assertEqual(store.close_calls, 0)

    def test_lazy_retriever_close_before_lookup_is_idempotent_and_does_not_construct(self) -> None:
        factory_calls: list[str] = []

        def factory() -> FakeRetriever:
            factory_calls.append("constructed")
            return FakeRetriever(QuestionRetrievalResult(status="unavailable"))

        retriever = LazyQuestionRetriever(factory)
        retriever.close()
        retriever.close()

        self.assertEqual(factory_calls, [])

    def test_lazy_retriever_closes_initialized_retriever_once(self) -> None:
        underlying = CloseSpy()
        lazy = LazyQuestionRetriever(lambda: underlying)
        intent = QuestionRetrievalIntent(
            query_text="test retrieval",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="intermediate",
        )

        result = lazy.retrieve(intent)
        lazy.close()
        lazy.close()

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(underlying.close_calls, 1)

    def test_owned_app_shutdown_closes_question_retriever(self) -> None:
        question_retriever = CloseSpy()
        container = WebContainer.for_test(
            repository=CloseSpy(),
            pre_interview_graph=object(),
            dispatcher=CloseSpy(),
            interview_graph=object(),
            question_retriever=question_retriever,
        )
        with patch("profile_agent.web.app.WebContainer.default", return_value=container):
            app = create_app()

        with TestClient(app):
            pass

        self.assertEqual(question_retriever.close_calls, 1)

    def test_owned_app_shutdown_cascades_through_initialized_lazy_retriever(self) -> None:
        embedding = CloseSpy()
        store = CloseSpy()
        inner = QuestionRetriever(
            embedding_client=embedding,
            store=store,
            owns_embedding_client=True,
            owns_store=True,
        )
        lazy = LazyQuestionRetriever(lambda: inner)
        intent = QuestionRetrievalIntent(
            query_text="test retrieval",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="intermediate",
        )
        lazy.retrieve(intent)
        container = WebContainer.for_test(
            repository=CloseSpy(),
            pre_interview_graph=object(),
            dispatcher=CloseSpy(),
            interview_graph=object(),
            question_retriever=lazy,
        )
        with patch("profile_agent.web.app.WebContainer.default", return_value=container):
            app = create_app()

        with TestClient(app):
            pass

        self.assertEqual(embedding.close_calls, 1)
        self.assertEqual(store.close_calls, 1)

    @staticmethod
    def _default_env(root: str, *, index_path: str = "") -> dict[str, str]:
        base = Path(root)
        return {
            "WEB_DATABASE_PATH": str(base / "web.sqlite3"),
            "WEB_CHECKPOINT_PATH": str(base / "checkpoints.sqlite3"),
            "QUESTION_RAG_INDEX_PATH": index_path,
        }

    @staticmethod
    def _close_default_container(container: WebContainer) -> None:
        for resource in (
            container.dispatcher,
            container.repository,
            container.checkpoint_connection,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    def test_default_container_wires_configured_retriever_lazily(self) -> None:
        retriever = FakeRetriever(QuestionRetrievalResult(status="unavailable"))
        factory_calls: list[str] = []

        def factory() -> FakeRetriever:
            factory_calls.append("constructed")
            return retriever

        with TemporaryDirectory() as root:
            env = self._default_env(root, index_path=str(Path(root) / "questions"))
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "profile_agent.graphs.interview.generate_question",
                    new=FakeQuestionGenerator(),
                ),
                patch(
                    "profile_agent.graphs.interview.generate_assessment_report",
                    new=lambda **_: make_test_report(),
                ),
                patch(
                    "profile_agent.graphs.interview._utc_now",
                    new=lambda: self.NOW,
                ),
            ):
                container = WebContainer.default(
                    question_retriever_factory=factory,
                )
                try:
                    self.assertIsNotNone(container.question_retriever)
                    self.assertEqual(factory_calls, [])

                    result = container.interview_graph.invoke(
                        self.initial_state(),
                        {"configurable": {"thread_id": "default-wired"}},
                    )
                    self.assertEqual(factory_calls, ["constructed"])
                    self.assertEqual(len(retriever.calls), 1)
                    self.assertEqual(
                        self.interrupt_payload(result)["question"],
                        "生成问题 1",
                    )
                finally:
                    self._close_default_container(container)

    def test_default_container_without_rag_config_stays_askable_without_http(self) -> None:
        with TemporaryDirectory() as root:
            env = self._default_env(root)
            with (
                patch.dict(os.environ, env, clear=False),
                patch("httpx.Client", side_effect=AssertionError("HTTP at startup")),
                patch(
                    "profile_agent.graphs.interview.generate_question",
                    new=FakeQuestionGenerator(),
                ),
                patch(
                    "profile_agent.graphs.interview.generate_assessment_report",
                    new=lambda **_: make_test_report(),
                ),
                patch(
                    "profile_agent.graphs.interview._utc_now",
                    new=lambda: self.NOW,
                ),
            ):
                container = WebContainer.default()
                try:
                    self.assertIsNone(container.question_retriever)
                    result = container.interview_graph.invoke(
                        self.initial_state(),
                        {"configurable": {"thread_id": "default-no-rag"}},
                    )
                    self.assertEqual(
                        self.interrupt_payload(result)["question"],
                        "生成问题 1",
                    )
                finally:
                    self._close_default_container(container)

    def test_default_container_env_wiring_missing_key_stays_lazy_and_unavailable(self) -> None:
        with TemporaryDirectory() as root:
            env = self._default_env(
                root,
                index_path=str(Path(root) / "questions"),
            )
            env["SILICONFLOW_API_KEY"] = ""
            with (
                patch.dict(os.environ, env, clear=False),
                patch("httpx.Client", side_effect=AssertionError("HTTP before provider is ready")),
                patch(
                    "profile_agent.graphs.interview.generate_question",
                    new=FakeQuestionGenerator(),
                ),
                patch(
                    "profile_agent.graphs.interview.generate_assessment_report",
                    new=lambda **_: make_test_report(),
                ),
                patch(
                    "profile_agent.graphs.interview._utc_now",
                    new=lambda: self.NOW,
                ),
            ):
                container = WebContainer.default()
                try:
                    self.assertIsNotNone(container.question_retriever)
                    result = container.interview_graph.invoke(
                        self.initial_state(),
                        {"configurable": {"thread_id": "default-missing-key"}},
                    )
                    payload = self.interrupt_payload(result)
                    state = container.interview_graph.get_state(
                        {"configurable": {"thread_id": "default-missing-key"}}
                    ).values
                    self.assertEqual(payload["question"], "生成问题 1")
                    self.assertEqual(
                        state["interview_turns"][0].retrieval_trace.status,
                        "unavailable",
                    )
                finally:
                    self._close_default_container(container)

    def test_env_factory_closes_embedding_when_store_construction_fails(self) -> None:
        embedding = CloseSpy()
        store_error = RuntimeError("private store construction failure")

        with TemporaryDirectory() as root:
            env = self._default_env(
                root,
                index_path=str(Path(root) / "questions"),
            )
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=embedding,
                ),
                patch(
                    "profile_agent.knowledge.qdrant_question_store.QdrantQuestionStore",
                    side_effect=store_error,
                ),
            ):
                factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(factory)
                with self.assertRaisesRegex(RuntimeError, "private store construction failure"):
                    factory()

        self.assertEqual(embedding.close_calls, 1)

    def test_env_factory_loads_verified_catalog_and_full_fingerprint(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "question_rag"
            / "minimal_question_bank.json"
        )
        with TemporaryDirectory() as root:
            root_path = Path(root)
            bank_path = root_path / "questions.json"
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            payload["test_only"] = False
            for question in payload["questions"]:
                question["source_type"] = "public_interview_experience"
                question["trust_level"] = "high"
            bank_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            env = self._default_env(root, index_path=str(root_path / "index"))
            env.update(
                {
                    "QUESTION_RAG_BANK_PATH": str(bank_path),
                    "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                    "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                    "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                    "QUESTION_RAG_INDEX_VERSION": "questions-v2",
                    "SILICONFLOW_API_KEY": "fake-key",
                }
            )
            embedding = CloseSpy()
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=embedding,
                ),
                patch(
                    "profile_agent.knowledge.qdrant_question_store.QdrantQuestionStore",
                    return_value=object(),
                ) as store_cls,
                patch(
                    "profile_agent.services.question_retrieval_service.QuestionRetriever",
                    return_value=object(),
                ),
            ):
                factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(factory)
                factory()

            store_kwargs = store_cls.call_args.kwargs
            catalog = store_kwargs["authoritative_catalog"]
            self.assertEqual(len(catalog), 6)
            fingerprint = store_kwargs["expected_fingerprint"]
            self.assertEqual(
                {
                    "provider",
                    "model",
                    "dimension",
                    "index_version",
                    "embedding_text_version",
                    "question_bank_manifest_hash",
                    "mode_policy_version",
                },
                set(fingerprint.model_dump()),
            )
            self.assertEqual(fingerprint.embedding_text_version, "six-section-v1")
            self.assertTrue(fingerprint.question_bank_manifest_hash.startswith("sha256:"))
            self.assertEqual(fingerprint.mode_policy_version, "2026-H2")

    def test_env_factory_without_catalog_keeps_persisted_reader_honest(self) -> None:
        with TemporaryDirectory() as root:
            env = self._default_env(root, index_path=str(Path(root) / "questions"))
            env.update(
                {
                    "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                    "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                    "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                    "SILICONFLOW_API_KEY": "fake-key",
                }
            )
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=CloseSpy(),
                ),
                patch(
                    "profile_agent.knowledge.qdrant_question_store.QdrantQuestionStore",
                    return_value=object(),
                ) as store_cls,
                patch(
                    "profile_agent.services.question_retrieval_service.QuestionRetriever",
                    return_value=object(),
                ),
            ):
                factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(factory)
                factory()

            self.assertEqual(store_cls.call_args.kwargs["authoritative_catalog"], {})
            fingerprint = store_cls.call_args.kwargs["expected_fingerprint"]
            self.assertEqual(fingerprint.embedding_text_version, "six-section-v1")
            self.assertEqual(
                fingerprint.question_bank_manifest_hash,
                "unavailable:canonical-question-bank",
            )

    def test_env_factory_accepts_legacy_v1_manifest_fixture(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "question_rag"
            / "legacy_v1_question.json"
        )
        manifest_fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "question_rag"
            / "legacy_v1_manifest.json"
        )
        with TemporaryDirectory() as root:
            root_path = Path(root)
            bank_path = root_path / "questions.json"
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            payload["test_only"] = False
            payload["questions"][0]["source_type"] = "public_interview_experience"
            bank_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            bank_path.with_name("QuestionBankManifest.json").write_text(
                manifest_fixture_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env = self._default_env(root, index_path=str(root_path / "index"))
            env.update(
                {
                    "QUESTION_RAG_BANK_PATH": str(bank_path),
                    "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                    "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                    "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                    "SILICONFLOW_API_KEY": "fake-key",
                }
            )
            embedding = CloseSpy()
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=embedding,
                ),
                patch(
                    "profile_agent.knowledge.qdrant_question_store.QdrantQuestionStore",
                    return_value=object(),
                ) as store_cls,
                patch(
                    "profile_agent.services.question_retrieval_service.QuestionRetriever",
                    return_value=object(),
                ),
            ):
                factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(factory)
                factory()

            self.assertEqual(len(store_cls.call_args.kwargs["authoritative_catalog"]), 1)
            fingerprint = store_cls.call_args.kwargs["expected_fingerprint"]
            self.assertTrue(fingerprint.question_bank_manifest_hash.startswith("sha256:"))

    def test_web_restart_recovers_persisted_hit_from_authoritative_catalog(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "question_rag"
            / "minimal_question_bank.json"
        )

        class StableEmbedding:
            provider = "fake-provider"
            model = "fake-model"
            dimension = 3

            def embed(self, texts):
                return [[1.0, 0.0, 0.0] for _ in texts]

        with TemporaryDirectory() as root:
            root_path = Path(root)
            bank_path = root_path / "questions.json"
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            payload["test_only"] = False
            for question in payload["questions"]:
                question["source_type"] = "public_interview_experience"
                question["trust_level"] = "high"
            bank_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            index_path = root_path / "index"
            env = self._default_env(root, index_path=str(index_path))
            env.update(
                {
                    "QUESTION_RAG_BANK_PATH": str(bank_path),
                    "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                    "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                    "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                    "QUESTION_RAG_INDEX_VERSION": "questions-v2",
                }
            )

            write_code = run_question_bank.main(
                ["rebuild", "--bank", str(bank_path), "--apply"],
                env=env,
                embedding_factory=lambda **_: StableEmbedding(),
            )
            self.assertEqual(write_code, 0)

            intent = QuestionRetrievalIntent(
                query_text="验证 Agent 的失败恢复",
                role="ai_agent_engineer",
                dimension_id="role_dim_01",
                question_mode="scenario",
                difficulty="intermediate",
            )
            with patch.dict(os.environ, env, clear=False), patch(
                "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                side_effect=lambda: StableEmbedding(),
            ):
                first_factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(first_factory)
                first_retriever = first_factory()
                first_result = first_retriever.retrieve(intent, today=date(2026, 8, 26))
                self.assertEqual(first_result.status, "hit")
                self.assertEqual(
                    first_result.selected_question.record.question_id,
                    "q_agent_001",
                )
                self.assertEqual(
                    first_result.selected_question.record.expected_signals,
                    ["状态机", "重试边界"],
                )
                first_retriever.close()

                # A second reader has only the persisted vectors/manifest plus
                # the freshly validated bank; it must recover the full record,
                # rather than treating clipped Qdrant payload as canonical.
                second_factory = _question_retriever_factory_from_env()
                self.assertIsNotNone(second_factory)
                second_retriever = second_factory()
                try:
                    second_result = second_retriever.retrieve(
                        intent,
                        today=date(2026, 8, 26),
                    )
                    self.assertEqual(second_result.status, "hit")
                    self.assertEqual(
                        second_result.selected_question.record.question_text,
                        payload["questions"][0]["question_text"],
                    )
                finally:
                    second_retriever.close()

    def test_default_container_factory_and_retrieval_errors_degrade_safely(self) -> None:
        class RaisingRetriever:
            def retrieve(self, _intent):
                raise RuntimeError("private retrieval failure")

        factories = [
            lambda: (_ for _ in ()).throw(RuntimeError("private construction failure")),
            lambda: RaisingRetriever(),
        ]
        for index, factory in enumerate(factories):
            with self.subTest(index=index), TemporaryDirectory() as root:
                env = self._default_env(
                    root,
                    index_path=str(Path(root) / "questions"),
                )
                with (
                    patch.dict(os.environ, env, clear=False),
                    patch(
                        "profile_agent.graphs.interview.generate_question",
                        new=FakeQuestionGenerator(),
                    ),
                    patch(
                        "profile_agent.graphs.interview.generate_assessment_report",
                        new=lambda **_: make_test_report(),
                    ),
                    patch(
                        "profile_agent.graphs.interview._utc_now",
                        new=lambda: self.NOW,
                    ),
                ):
                    container = WebContainer.default(
                        question_retriever_factory=factory,
                    )
                    try:
                        result = container.interview_graph.invoke(
                            self.initial_state(),
                            {"configurable": {"thread_id": f"default-error-{index}"}},
                        )
                        self.assertEqual(
                            self.interrupt_payload(result)["question"],
                            "生成问题 1",
                        )
                        state = container.interview_graph.get_state(
                            {"configurable": {"thread_id": f"default-error-{index}"}}
                        ).values
                        self.assertEqual(
                            state["interview_turns"][0].retrieval_trace.status,
                            "unavailable",
                        )
                    finally:
                        self._close_default_container(container)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from profile_agent.model_runtime import ModelRuntimeRegistry, ModelScopedGraph
from profile_agent.web.container import WebContainer
from profile_agent.web.routers.assessments import router as assessments_router
from profile_agent.web.routers.demo import router as demo_router
from profile_agent.web.routers.interviews import router as interviews_router
from profile_agent.web.routers.models import router as models_router


def create_app(container: WebContainer | None = None) -> FastAPI:
    owns_container = container is None
    resolved_container = WebContainer.default() if owns_container else container
    if not hasattr(resolved_container, "model_runtime_registry"):
        resolved_container.model_runtime_registry = ModelRuntimeRegistry()
    if resolved_container.interview_graph is not None and not isinstance(
        resolved_container.interview_graph,
        ModelScopedGraph,
    ):
        def requires_custom_config(assessment_id: str) -> bool:
            try:
                record = resolved_container.repository.get(assessment_id)
            except KeyError:
                return False
            return record.model_session_id is not None

        resolved_container.interview_graph = ModelScopedGraph(
            resolved_container.interview_graph,
            resolved_container.model_runtime_registry,
            requires_custom_config=requires_custom_config,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if app.state.container_owned:
                first_error: BaseException | None = None
                for resource in (
                    app.state.container.scenario_retriever,
                    app.state.container.question_retriever,
                    app.state.container.dispatcher,
                    app.state.container.repository,
                    app.state.container.checkpoint_connection,
                ):
                    close = getattr(resource, "close", None)
                    if not callable(close):
                        continue
                    try:
                        close()
                    except BaseException as error:
                        if first_error is None:
                            first_error = error
                if first_error is not None:
                    raise first_error

    app = FastAPI(title="衡鉴 Evidence Hiring", lifespan=lifespan)
    app.state.container = resolved_container
    app.state.container_owned = owns_container
    app.include_router(models_router, prefix="/api")
    app.include_router(assessments_router, prefix="/api")
    app.include_router(interviews_router, prefix="/api")
    app.include_router(demo_router, prefix="/api")
    return app

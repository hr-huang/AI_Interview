from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from profile_agent.web.container import WebContainer
from profile_agent.web.routers.assessments import router as assessments_router
from profile_agent.web.routers.demo import router as demo_router
from profile_agent.web.routers.interviews import router as interviews_router


def create_app(container: WebContainer | None = None) -> FastAPI:
    owns_container = container is None
    resolved_container = WebContainer.default() if owns_container else container

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if app.state.container_owned:
                # Stop background work before closing the database it may use.
                first_error: BaseException | None = None
                for resource in (
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
    app.include_router(assessments_router, prefix="/api")
    app.include_router(interviews_router, prefix="/api")
    app.include_router(demo_router, prefix="/api")
    return app

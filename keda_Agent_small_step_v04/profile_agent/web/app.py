from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from profile_agent.web.container import WebContainer
from profile_agent.web.routers.assessments import router as assessments_router


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
                dispatcher_close = getattr(
                    app.state.container.dispatcher,
                    "close",
                    None,
                )
                repository_close = getattr(
                    app.state.container.repository,
                    "close",
                    None,
                )
                try:
                    if callable(dispatcher_close):
                        dispatcher_close()
                finally:
                    if callable(repository_close):
                        repository_close()

    app = FastAPI(title="衡鉴 Evidence Hiring", lifespan=lifespan)
    app.state.container = resolved_container
    app.state.container_owned = owns_container
    app.include_router(assessments_router, prefix="/api")
    return app

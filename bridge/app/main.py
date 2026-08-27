"""FastAPI app factory.

The stash-box PerformerTagger relay (bridge.app.stashbox) at /graphql — Stash's native
metadata-provider surface — plus the name-DB / enrichment / harvest HTTP APIs and /healthz.
"""

import strawberry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.schema.config import StrawberryConfig

from bridge.app.api import assets as assets_api
from bridge.app.api import enrichment as enrichment_api
from bridge.app.api import harvest as harvest_api
from bridge.app.api import names as names_api
from bridge.app.api import scrape as scrape_api
from bridge.app.api import thumbnails as thumbnails_api
from bridge.app.config import get_settings
from bridge.app.stashbox import SERVICE_NAME, Query


def create_app() -> FastAPI:
    get_settings()  # fail fast on bad config
    schema = strawberry.Schema(
        query=Query,
        config=StrawberryConfig(auto_camel_case=False),  # match stash-box snake_case SDL
    )
    app = FastAPI(title=f"{SERVICE_NAME} metadata provider")

    # The tagger page (browser, Stash origin) calls the name-DB API cross-origin (DESIGN §12).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(GraphQLRouter(schema), prefix="/graphql")
    app.include_router(names_api.router)
    app.include_router(harvest_api.router)
    app.include_router(assets_api.router)
    app.include_router(scrape_api.router)
    app.include_router(enrichment_api.router)
    app.include_router(thumbnails_api.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME}

    return app


app = create_app()

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.core.config import get_settings
from app.core.python_compat import validate_python_version
from app.core.database import init_db
from app.middleware.error_handler import add_exception_handlers
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.locale import LocaleMiddleware


def create_app() -> FastAPI:
    validate_python_version()
    settings = get_settings()
    app = FastAPI(
        title="Hari Santri 2026 Event Portal API",
        description=(
            "Hari Santri event API with Indonesian (`id`) and English (`en`) support. "
            "Use the `locale` query parameter or `Accept-Language`; query takes priority. "
            "Machine values such as status, error codes, providers, and allowed actions remain canonical."
        ),
        debug=settings.APP_DEBUG,
        version="1.0.0",
    )

    if settings.CORS_ENABLED:
        allowed_origins = [
            origin.strip()
            for origin in settings.FRONTEND_URL.split(",")
            if origin.strip()
        ]

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins or ["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    add_exception_handlers(app)

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(LocaleMiddleware)

    app.include_router(router, prefix=settings.API_PREFIX)
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    app.mount(settings.UPLOAD_URL_PREFIX, StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

    def bilingual_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("parameters", {})["LocaleQuery"] = {
            "name": "locale",
            "in": "query",
            "required": False,
            "description": "Response locale. Supported values: id and en. Overrides Accept-Language.",
            "schema": {"type": "string", "enum": ["id", "en"], "default": "id"},
        }
        for path_item in schema.get("paths", {}).values():
            for method in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not operation:
                    continue
                parameters = operation.setdefault("parameters", [])
                if not any(parameter.get("name") == "locale" or parameter.get("$ref", "").endswith("/LocaleQuery") for parameter in parameters):
                    parameters.append({"$ref": "#/components/parameters/LocaleQuery"})
        app.openapi_schema = schema
        return schema

    app.openapi = bilingual_openapi

    @app.on_event("startup")
    async def startup() -> None:
        await init_db()
        logging.getLogger(__name__).info("Application startup completed")

    return app


app = create_app()

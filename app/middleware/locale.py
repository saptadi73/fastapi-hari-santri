import logging
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.i18n import request_locale

logger = logging.getLogger("app.requests")


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        started_at = perf_counter()
        locale = request_locale(request)
        request.state.locale = locale
        response = await call_next(request)
        response.headers["Content-Language"] = locale
        logger.info(
            "request_completed",
            extra={
                "request_id": getattr(request.state, "request_id", ""),
                "locale": locale,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )
        return response

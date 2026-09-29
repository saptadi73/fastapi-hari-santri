from datetime import datetime, timezone
from typing import Any

from fastapi import Request

from app.core.i18n import request_locale, translate_error_message, translate_message


def _request_id(request: Request | None) -> str:
    if request is None:
        return ""
    return getattr(request.state, "request_id", "") or ""


def success_response(message: str, data: Any | None = None, meta: Any | None = None, request: Request | None = None):
    locale = request_locale(request)
    localized_message = translate_message(message, locale)
    if localized_message == message and locale == "en" and message not in {"OK", "Created", "Accepted"}:
        localized_message = "Success"
    return {
        "success": True,
        "message": localized_message,
        "data": data,
        "meta": meta,
        "request_id": _request_id(request),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def fail_response(message: str, errors: list[dict] | None = None, request: Request | None = None):
    locale = request_locale(request)
    source_errors = errors or []
    localized_errors = [
        {**error, "message": translate_error_message(str(error.get("code", "")), str(error.get("message", "")), locale)}
        for error in source_errors
    ]
    top_code = str(source_errors[0].get("code", "")) if source_errors else None
    return {
        "success": False,
        "message": translate_error_message(top_code, message, locale),
        "errors": localized_errors,
        "request_id": _request_id(request),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

"""Optional actual-framework factory; importing this module starts nothing.

Actual FastAPI/httpx integration and listener/browser lifecycle need their own
dependency/resource checks. No substitute framework or automatic installation.
"""

import asyncio
import json

from .access import LocalAccessPolicy
from .asgi_guard import LocalASGIGuard
from .models import ComparisonInputError, parse_page_query
from .service import ComparisonService, ComparisonServiceError


class APICapabilityUnavailable(RuntimeError):
    pass


def create_app(policy: LocalAccessPolicy, *, service: ComparisonService | None = None):
    try:
        from fastapi import FastAPI, Request, Response
        from fastapi.exceptions import RequestValidationError
        from starlette.exceptions import HTTPException
    except ImportError as error:
        raise APICapabilityUnavailable("fastapi_dependency_unavailable") from error

    if type(policy) is not LocalAccessPolicy or (
        service is not None and type(service) is not ComparisonService
    ):
        raise ValueError("explicit_local_policy_and_service_required")
    service = service if service is not None else ComparisonService()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def response(code, *, status=422):
        return Response(
            json.dumps({"code": code}, separators=(",", ":")).encode(),
            status_code=status,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return response("invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return response(
            "route_not_found" if error.status_code == 404 else "invalid_request",
            status=404 if error.status_code == 404 else 400,
        )

    async def perform(call):
        try:
            data = await asyncio.to_thread(call)
        except ComparisonInputError:
            return response("invalid_comparison_upload")
        except ComparisonServiceError as error:
            if str(error) == "comparison_not_found":
                return response("comparison_not_found", status=404)
            return response("comparison_input_or_page_invalid")
        except Exception:
            # Do not serialize arbitrary exception messages or input-bearing
            # Pydantic errors; an explicit failed request is not a match result.
            return response("comparison_failed", status=500)
        return Response(data, media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/api/comparisons")
    async def create_comparison(request: Request):
        body = await request.body()  # supplied only after the outer bounded receiver
        return await perform(lambda: service.create(body))

    @app.get("/api/comparisons/{workspace_id}/review-page")
    async def review_page(workspace_id: str, request: Request):
        try:
            query = parse_page_query(request.scope.get("query_string", b""))
        except ComparisonInputError:
            return response("invalid_page_query")
        return await perform(lambda: service.page(workspace_id, **query))

    @app.get("/api/status")
    async def status():
        return Response(
            json.dumps(service.status(), separators=(",", ":")).encode(),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    return LocalASGIGuard(app, policy)

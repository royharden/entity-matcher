"""Explicit single-process loopback API launch; optional packages stay lazy."""

from collections.abc import Mapping

from .access import LocalAccessPolicy
from .fastapi_app import APICapabilityUnavailable, create_app

REVIEW_TOKEN_ENV = "ENTITY_MATCHER_REVIEW_TOKEN"


class APILaunchError(ValueError):
    """Closed launch codes, without environment values or arbitrary exception text."""


def policy_from_environment(
    port: int, frontend_ports: tuple[int, ...], environment: Mapping[str, str]
) -> LocalAccessPolicy:
    token = environment.get(REVIEW_TOKEN_ENV, "")
    if not token:
        raise APILaunchError("review_token_required")
    try:
        return LocalAccessPolicy(port, token, frontend_ports)
    except ValueError:
        raise APILaunchError("api_launch_configuration_invalid") from None


def make_server(policy: LocalAccessPolicy, *, max_requests: int | None = None):
    if type(policy) is not LocalAccessPolicy or (
        max_requests is not None
        and (type(max_requests) is not int or not 1 <= max_requests <= 10000)
    ):
        raise APILaunchError("api_launch_configuration_invalid")
    try:
        import uvicorn
    except ImportError:
        raise APICapabilityUnavailable("uvicorn_dependency_unavailable") from None
    app = create_app(policy)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=policy.api_port,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="off",
        workers=1,
        reload=False,
        proxy_headers=False,
        forwarded_allow_ips="",
        access_log=False,
        log_config=None,
        log_level="critical",
        server_header=False,
        limit_concurrency=8,
        limit_max_requests=max_requests,
        timeout_keep_alive=2,
        timeout_graceful_shutdown=2,
        backlog=16,
    )
    return uvicorn.Server(config)


def run_server(policy: LocalAccessPolicy, *, max_requests: int | None = None) -> dict:
    server = make_server(policy, max_requests=max_requests)
    try:
        server.run()
    except KeyboardInterrupt:
        if not server.started:
            raise APILaunchError("api_start_interrupted") from None
    except (OSError, SystemExit):
        raise APILaunchError("api_listener_unavailable") from None
    except Exception:
        raise APILaunchError("api_server_failed") from None
    if not server.started:
        raise APILaunchError("api_listener_unavailable")
    return {
        "stage": "serve",
        "status": "stopped",
        "bind": "127.0.0.1",
        "port": policy.api_port,
        "human_rulings_available": False,
        "provider_dispatch_available": False,
        "persistent_journal_writes_available": False,
    }

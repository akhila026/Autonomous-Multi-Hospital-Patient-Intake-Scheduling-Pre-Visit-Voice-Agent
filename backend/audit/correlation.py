import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable storing correlation ID for the active request thread/task
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

def get_correlation_id() -> str:
    """Returns the current request's correlation ID or creates a fallback."""
    cid = correlation_id_ctx.get()
    return cid if cid else str(uuid.uuid4())

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware attaching an X-Correlation-ID to incoming and outgoing HTTP calls,
    allowing end-to-end tracing across AI, scheduling, EHR sync, and workflows.
    """
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        token = correlation_id_ctx.set(correlation_id)
        try:
            response: Response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            correlation_id_ctx.reset(token)

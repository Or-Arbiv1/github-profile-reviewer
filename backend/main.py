import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.errors import AnalyzeError
from backend.routers import analyze
from backend.services import github

logger = logging.getLogger("github_profile_reviewer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing to do (the shared GitHub client is created lazily on first use).
    yield
    # Shutdown cleanup: close the pooled GitHub client so its connections are released
    # cleanly instead of being torn down by the GC (avoids unclosed-client warnings).
    await github.aclose_client()


app = FastAPI(lifespan=lifespan)

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.exception_handler(AnalyzeError)
async def analyze_error_handler(request: Request, exc: AnalyzeError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Safety net: any failure we didn't map to an AnalyzeError still returns the uniform
    # error shape the frontend expects — never a bare 500 or a leaked stack trace.
    # The real detail is logged server-side; the client gets a generic, actionable message.
    logger.exception("Unhandled error during request to %s", request.url.path)
    return JSONResponse(
        status_code=502,
        content={"error": {
            "code": "upstream",
            "message": "Something went wrong reaching an upstream service. Try again.",
        }},
    )


app.include_router(analyze.router)
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="static")

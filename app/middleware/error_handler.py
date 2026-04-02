import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        err = dict(error)
        if "ctx" in err and isinstance(err["ctx"], dict):
            err["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        errors.append(err)
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "details": errors},
    )


async def general_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )

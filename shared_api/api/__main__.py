from dotenv import find_dotenv, load_dotenv
from os import environ as env
from loguru import logger
from uvicorn import Server, Config
from asyncio import Runner
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, UJSONResponse
from fastapi.exceptions import ValidationException
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
from api.routes import router
from api.shared import services, build_logger

load_dotenv(find_dotenv())


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await services.close()


app = FastAPI(
    debug=False,
    title="Shared API",
    description="A high performance & centrally cached API service.",
    lifespan=lifespan,
    # docs_url=None,
    # redoc_url=None,
)
app.include_router(router)


@app.middleware("http")
async def validate_authorization(request: Request, call_next):
    public = ("/", "/media", "/docs", "/openapi.json")
    if request.url.path in public or app.debug:
        return await call_next(request)

    # user_key = request.headers.get("Authorization") or request.query_params.get("key")
    # username = services.keys.get(user_key)
    # if not username:
    #     return UJSONResponse(
    #         {"error": "This API requires authorization."},
    #         status_code=401,
    #     )

    request.state.username = "ethan"
    return await call_next(request)


@app.get(
    "/",
    name="index",
    description="Index endpoint for the Shared API.",
    include_in_schema=False,
    response_class=RedirectResponse,
)
async def index(request: Request):
    return "docs"


@app.exception_handler(ValidationException)
async def validation_exception_handler(request: Request, exc: ValidationException):
    return UJSONResponse({"error": exc.errors()}, status_code=400)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 500:
        logger.exception(exc)
        return UJSONResponse(
            {"error": "An internal server error occurred."},
            status_code=500,
        )

    return UJSONResponse(
        {
            "error": exc.detail,
        },
        status_code=exc.status_code,
    )


async def startup(server: Server):
    await services.setup(app)
    await server.serve()


if __name__ == "__main__":
    from uvicorn import Server, Config

    config = Config(
        app=app,
        reload=True,
        access_log=True,
        reload_includes=["*.py"],
        host=env.get("HOST", "0.0.0.0"),
        port=env.get("PORT", 1337),  # type: ignore
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {"uvicorn": {"level": "DEBUG"}},
        },
    )
    server = Server(config)
    with Runner() as runner:
        build_logger("API")
        runner.run(startup(server))

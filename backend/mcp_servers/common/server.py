from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


def create_mcp(name: str, port: int) -> FastMCP:
    server = FastMCP(
        name,
        host="0.0.0.0",
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": name})

    return server


def as_json(call: Callable[..., object]) -> Callable[..., object]:
    return call

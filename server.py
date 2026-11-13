from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
import json
import os
from pathlib import Path

mcp = FastMCP("nolga-mcp")

# Store API keys in a local file. Override the location with NOLGA_API_KEYS_FILE
# (e.g. point it at a mounted volume when running in a container).
API_KEYS_FILE = Path(
    os.getenv(
        "NOLGA_API_KEYS_FILE",
        str(Path.home() / ".config" / "nolga-mcp" / "api_keys.json"),
    )
)


def _load_api_keys():
    keys = {}
    try:
        if API_KEYS_FILE.exists():
            with open(API_KEYS_FILE, "r") as f:
                keys = json.load(f)
    except Exception:
        keys = {}
    # NOLGA_API_KEY takes precedence over any key stored on disk.
    env_key = os.getenv("NOLGA_API_KEY")
    if env_key:
        keys["backend_api_key"] = env_key
    return keys


def _save_api_keys(keys):
    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(API_KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


def _key_from_headers(headers):
    """Extract a backend API key from request headers.

    Supports `Authorization: Bearer <key>` and the `X-Nolga-Api-Key` /
    `X-Api-Key` headers. Returns None when no key is present.
    """
    lowered = {str(name).lower(): value for name, value in headers.items()}

    authorization = lowered.get("authorization")
    if authorization:
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            return token.strip() or None
        return authorization.strip() or None

    for name in ("x-nolga-api-key", "x-api-key"):
        value = lowered.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _resolve_api_key():
    """Return the backend API key for the calling client.

    Over HTTP each client must present its own key via headers, so keys are
    never shared between clients. For local/stdio use the server falls back to
    NOLGA_API_KEY or the local key file.
    """
    try:
        request = get_http_request()
    except RuntimeError:
        return os.getenv("NOLGA_API_KEY") or _load_api_keys().get("backend_api_key")
    return _key_from_headers(request.headers)


@mcp.tool
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.resource("greeting://{name}")
def greeting_resource(name: str) -> str:
    """Get a greeting for a name."""
    return f"Hello, {name}! Welcome to nolga-mcp."


@mcp.tool
def whoami() -> str:
    """Report whether this client's backend API key is configured.

    The full key is never returned. Each client supplies its own key through
    its MCP connection headers, so clients are isolated from one another.
    """
    api_key = _resolve_api_key()
    if not api_key:
        return (
            "No backend API key. Configure 'Authorization: Bearer <key>' in "
            "this client's MCP server headers."
        )
    suffix = api_key[-4:] if len(api_key) >= 4 else "****"
    return f"Backend API key configured (ending in {suffix})."


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    mcp.run(
        transport=os.getenv("MCP_TRANSPORT", "http"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        stateless_http=_env_bool("MCP_STATELESS_HTTP", True),
    )
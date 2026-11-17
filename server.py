from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

mcp = FastMCP("nolga-mcp")

BACKEND_BASE_URL = os.getenv(
    "NOLGA_BACKEND_URL", "https://nolga-backend-develop.onrender.com"
).rstrip("/")
BACKEND_TIMEOUT = int(os.getenv("NOLGA_BACKEND_TIMEOUT", "60"))


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


def _backend_request(method, path, api_key, payload=None):
    """Send a request to the nolga backend using the caller's API key.

    Returns a tuple of (status_code, parsed_body). Error responses are
    returned instead of raised so tools can report them back to the client.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BACKEND_BASE_URL}{path}", data=data, method=method
    )
    request.add_header("Accept", "application/json")
    request.add_header("X-API-Key", api_key)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=BACKEND_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return error.code, parsed
    except OSError as error:
        reason = getattr(error, "reason", error)
        return 0, {"detail": f"Backend unreachable: {reason}"}


def _format_backend_error(status, body):
    detail = body.get("detail") if isinstance(body, dict) else None
    if not detail:
        detail = json.dumps(body, ensure_ascii=False) if body else "no response body"
    return f"Backend request failed (HTTP {status}): {detail}"


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


@mcp.tool
def register_api_key(name: str) -> str:
    """Create a new backend API key for the calling client.

    Uses the client's current key (from the MCP connection headers) to
    authenticate against the backend, then returns the newly created key.
    """
    api_key = _resolve_api_key()
    if not api_key:
        return (
            "No backend API key. Configure 'Authorization: Bearer <key>' in "
            "this client's MCP server headers."
        )

    status, body = _backend_request("POST", "/api-keys", api_key, {"name": name})
    if status == 201 and isinstance(body, dict) and body.get("api_key"):
        return (
            f"API key registered: {body['api_key']}\n"
            f"id={body.get('id')} name={body.get('name')} "
            f"expires_at={body.get('expires_at')}\n"
            "Store this key now. It will not be shown again."
        )
    return _format_backend_error(status, body)


@mcp.tool
def verify_api_key(api_key: str | None = None) -> str:
    """Verify an API key against the backend.

    When api_key is omitted, the calling client's key from the MCP connection
    headers is verified.
    """
    key = api_key or _resolve_api_key()
    if not key:
        return (
            "No API key to verify. Pass api_key or configure "
            "'Authorization: Bearer <key>' in this client's MCP server headers."
        )

    status, body = _backend_request("GET", "/api-keys/verify", key)
    if status == 200 and isinstance(body, dict) and body.get("valid"):
        return f"API key is valid (api_key_id={body.get('api_key_id')})."
    return _format_backend_error(status, body)


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
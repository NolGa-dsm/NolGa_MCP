from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
import json
import os
import urllib.error
import urllib.parse
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
    if detail is None and body is not None:
        detail = body
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    if not detail:
        detail = "no response body"
    return f"Backend request failed (HTTP {status}): {detail}"


def _pretty(body):
    return json.dumps(body, ensure_ascii=False, indent=2, default=str)


_NO_KEY_MESSAGE = (
    "No backend API key. Configure 'Authorization: Bearer <key>' in "
    "this client's MCP server headers."
)

MAX_SUMMARY_LEN = 255
WORK_STATUSES = ("auto", "review", "edited")
CHANGE_TYPES = ("added", "modified", "deleted")
SOURCE_TYPES = ("commit", "issue", "file", "note")


def _as_list(value, field):
    """Accept a list or a JSON-encoded list (some clients send strings)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{field} must be a JSON array ({error})") from error
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _normalize_changes(value):
    items = _as_list(value, "changes")
    if items is None:
        return None
    normalized = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"changes[{index}] must be an object")
        path = str(item.get("path") or "").strip()
        if not path:
            raise ValueError(f"changes[{index}].path is required")
        if len(path) > 500:
            raise ValueError(f"changes[{index}].path must be 500 chars or fewer")
        change_type = item.get("type")
        if change_type not in CHANGE_TYPES:
            raise ValueError(
                f"changes[{index}].type must be one of {', '.join(CHANGE_TYPES)}"
            )

        counts = {}
        for name in ("additions", "deletions"):
            try:
                counts[name] = max(0, int(item.get(name) or 0))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"changes[{index}].{name} must be an integer"
                ) from error

        normalized.append(
            {
                "path": path,
                "type": change_type,
                "additions": counts["additions"],
                "deletions": counts["deletions"],
            }
        )
    return normalized


def _normalize_sources(value):
    items = _as_list(value, "sources")
    if items is None:
        return None
    normalized = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{index}] must be an object")
        source_type = item.get("type")
        if source_type not in SOURCE_TYPES:
            raise ValueError(
                f"sources[{index}].type must be one of {', '.join(SOURCE_TYPES)}"
            )
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"sources[{index}].title is required")
        if len(title) > 500:
            raise ValueError(f"sources[{index}].title must be 500 chars or fewer")
        normalized.append({"type": source_type, "title": title})
    return normalized


def _fit_summary(summary, request):
    """Keep summary inside the backend's 255-char column.

    The backend stores summary as VARCHAR(255) but request as TEXT, so a long
    summary is preserved by moving its full text into request. Returns
    (summary, request, note).
    """
    summary = summary or ""
    if len(summary) <= MAX_SUMMARY_LEN:
        return summary, request, None

    if not request:
        request = summary
        note = "summary exceeded 255 chars; full text moved to request."
    else:
        if summary not in request:
            request = f"{request}\n\n---\n\n{summary}"
        note = "summary exceeded 255 chars; full text appended to request."

    shortened = summary[: MAX_SUMMARY_LEN - 3].rstrip() + "..."
    return shortened, request, note


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
        return _NO_KEY_MESSAGE
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
        return _NO_KEY_MESSAGE

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


@mcp.tool
def create_work(
    title: str,
    summary: str,
    status: str = "auto",
    repository: str | None = None,
    branch: str | None = None,
    request: str | None = None,
    style_name: str | None = None,
    changes: list[dict] | str | None = None,
    sources: list[dict] | str | None = None,
) -> str:
    """Register a work item on the Nolga backend.

    Keep `summary` short (255 chars max). Put the long markdown handover or
    details in `request`; if `summary` is longer than 255 chars it is moved
    into `request` automatically. `changes` items look like {"path":
    "src/a.py", "type": "modified", "additions": 1, "deletions": 0}; `sources`
    items look like {"type": "commit", "title": "abc123 message"}.
    """
    if status not in WORK_STATUSES:
        return (
            f"Invalid status {status!r}; expected one of "
            f"{', '.join(WORK_STATUSES)}."
        )
    api_key = _resolve_api_key()
    if not api_key:
        return _NO_KEY_MESSAGE

    try:
        normalized_changes = _normalize_changes(changes)
        normalized_sources = _normalize_sources(sources)
    except ValueError as error:
        return f"Invalid work payload: {error}"

    summary, request, note = _fit_summary(summary, request)

    payload = {
        "title": title,
        "summary": summary,
        "status": status,
        "repository": repository,
        "branch": branch,
        "request": request,
        "styleName": style_name,
    }
    if normalized_changes:
        payload["changes"] = normalized_changes
    if normalized_sources:
        payload["sources"] = normalized_sources

    status_code, body = _backend_request("POST", "/api/works", api_key, payload)
    if status_code == 201:
        if note and isinstance(body, dict):
            body = {**body, "_note": note}
        return _pretty(body)
    if status_code == 500:
        return (
            "Backend request failed (HTTP 500). The work was not created. "
            "This usually means a field exceeds its DB column (summary is "
            "limited to 255 chars). Retry with a short summary and put the "
            "long text in request."
        )
    return _format_backend_error(status_code, body)


@mcp.tool
def list_works(
    page: int = 1,
    size: int = 20,
    q: str | None = None,
    status: str | None = None,
    repository: str | None = None,
    author: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> str:
    """List work items, newest first. Supports paging, search and filters."""
    api_key = _resolve_api_key()
    if not api_key:
        return _NO_KEY_MESSAGE

    params = {
        "page": page,
        "size": size,
        "q": q,
        "status": status,
        "repository": repository,
        "author": author,
        "created_from": created_from,
        "created_to": created_to,
    }
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value is not None}
    )
    path = f"/api/works?{query}" if query else "/api/works"

    status_code, body = _backend_request("GET", path, api_key)
    if status_code == 200:
        return _pretty(body)
    return _format_backend_error(status_code, body)


@mcp.tool
def get_work(work_id: str) -> str:
    """Fetch a single work item, including its changes and sources."""
    api_key = _resolve_api_key()
    if not api_key:
        return _NO_KEY_MESSAGE

    encoded = urllib.parse.quote(work_id, safe="")
    status_code, body = _backend_request("GET", f"/api/works/{encoded}", api_key)
    if status_code == 200:
        return _pretty(body)
    return _format_backend_error(status_code, body)


@mcp.tool
def update_work_summary(work_id: str, summary: str) -> str:
    """Update a work's summary. The backend marks the work as 'edited'."""
    api_key = _resolve_api_key()
    if not api_key:
        return _NO_KEY_MESSAGE

    encoded = urllib.parse.quote(work_id, safe="")
    status_code, body = _backend_request(
        "PATCH", f"/api/works/{encoded}", api_key, {"summary": summary}
    )
    if status_code == 200:
        return _pretty(body)
    return _format_backend_error(status_code, body)


WORK_GUIDE = """# Nolga 작업 등록 가이드

`create_work`를 호출할 때는 아래 규칙을 지켜 주세요.

## 핵심 규칙
1. `summary`는 한 줄, 255자 이내로 짧게 씁니다.
2. 긴 설명/인수인계서/마크다운은 전부 `request`에 넣습니다. (`request`는 길이 제한 없음)
3. `repository`, `branch`를 가능하면 채웁니다.
4. 실제 커밋의 변경 파일을 `changes`에 넣습니다.
5. 커밋/이슈/파일/노트는 `sources`에 넣습니다.
6. `status`는 보통 `auto`입니다.

## 호출 예시
```json
{
  "title": "로그인 버그 수정",
  "summary": "로그인 토큰 갱신 로직을 수정했습니다.",
  "request": "## 상세\\n- 원인: ...\\n- 변경: ...\\n- 테스트: ...",
  "status": "auto",
  "repository": "owner/repo",
  "branch": "main",
  "style_name": "handover",
  "changes": [{"path": "app/auth.py", "type": "modified", "additions": 10, "deletions": 2}],
  "sources": [{"type": "commit", "title": "abc123 로그인 토큰 갱신 수정"}]
}
```

## changes 만드는 법
```sh
git show --name-status --format='%H %s' <sha>
git show --numstat --format='' <sha>
```
- `A` -> `added`, `M` -> `modified`, `D` -> `deleted`
- `additions`/`deletions`는 numstat 값
- `path`는 저장소 루트 기준 상대 경로

## 등록 후 확인
- 반환된 `id`로 `get_work(id)`를 호출해 `summary`, `request`, `changes`, `sources`가 모두 저장됐는지 확인합니다.
- `summary`가 255자를 넘으면 서버가 자동으로 `request`로 옮기고 `summary`를 줄입니다. 그래도 처음부터 짧게 쓰는 것을 권장합니다.
"""


@mcp.prompt(
    name="nolga_work_guide",
    description="Nolga 작업 등록 규칙과 create_work 호출 예시",
)
def nolga_work_guide() -> str:
    return WORK_GUIDE


@mcp.tool
def work_registration_guide() -> str:
    """Return the Nolga work-registration guide.

    Call this before create_work to get the summary/request/changes rules and
    a ready-to-use payload example.
    """
    return WORK_GUIDE


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
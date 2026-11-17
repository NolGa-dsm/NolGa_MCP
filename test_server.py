import pytest
from fastmcp import FastMCP
import server
from server import mcp, _key_from_headers


@pytest.mark.asyncio
async def test_hello_tool():
    result = await mcp.call_tool("hello", {"name": "World"})
    # FastMCP returns ToolResult
    if hasattr(result, 'content'):
        assert result.content[0].text == "Hello, World!"
    else:
        assert result == "Hello, World!"


@pytest.mark.asyncio
async def test_add_tool():
    result = await mcp.call_tool("add", {"a": 2, "b": 3})
    if hasattr(result, 'content'):
        assert result.content[0].text == "5"
    else:
        assert result == 5


@pytest.mark.asyncio
async def test_greeting_resource():
    result = await mcp.read_resource("greeting://Test")
    if hasattr(result, 'contents'):
        assert "Hello, Test!" in result.contents[0].content
    else:
        assert "Hello, Test!" in result


def test_key_from_bearer_header():
    headers = {"Authorization": "Bearer client-a-key"}
    assert _key_from_headers(headers) == "client-a-key"


def test_key_from_bearer_header_case_insensitive():
    headers = {"authorization": "bearer client-b-key"}
    assert _key_from_headers(headers) == "client-b-key"


def test_key_from_custom_header():
    headers = {"X-Nolga-Api-Key": "client-c-key"}
    assert _key_from_headers(headers) == "client-c-key"


def test_key_missing_returns_none():
    assert _key_from_headers({}) is None


def test_register_api_key_success(monkeypatch):
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "nlk_prefix_secret")

    def fake_request(method, path, api_key, payload=None):
        assert method == "POST"
        assert path == "/api-keys"
        assert api_key == "nlk_prefix_secret"
        assert payload == {"name": "laptop"}
        return 201, {
            "id": 3,
            "name": "laptop",
            "expires_at": "2027-01-01T00:00:00Z",
            "api_key": "nlk_abc_new",
        }

    monkeypatch.setattr(server, "_backend_request", fake_request)
    result = server.register_api_key("laptop")
    assert "nlk_abc_new" in result
    assert "id=3" in result


def test_register_api_key_without_key(monkeypatch):
    monkeypatch.setattr(server, "_resolve_api_key", lambda: None)
    assert "No backend API key" in server.register_api_key("laptop")


def test_register_api_key_backend_error(monkeypatch):
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "nlk_prefix_secret")
    monkeypatch.setattr(
        server,
        "_backend_request",
        lambda *args, **kwargs: (401, {"detail": "Invalid authentication credentials"}),
    )
    result = server.register_api_key("laptop")
    assert "HTTP 401" in result
    assert "Invalid authentication credentials" in result


def test_verify_api_key_success(monkeypatch):
    monkeypatch.setattr(
        server,
        "_backend_request",
        lambda *args, **kwargs: (200, {"valid": True, "api_key_id": 7}),
    )
    result = server.verify_api_key("nlk_a_b")
    assert "valid" in result
    assert "api_key_id=7" in result


def test_verify_api_key_invalid(monkeypatch):
    monkeypatch.setattr(
        server,
        "_backend_request",
        lambda *args, **kwargs: (401, {"detail": "Invalid authentication credentials"}),
    )
    result = server.verify_api_key("bad")
    assert "HTTP 401" in result


def test_verify_api_key_without_any_key(monkeypatch):
    monkeypatch.setattr(server, "_resolve_api_key", lambda: None)
    assert "No API key to verify" in server.verify_api_key()
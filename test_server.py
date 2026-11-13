import pytest
from fastmcp import FastMCP
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
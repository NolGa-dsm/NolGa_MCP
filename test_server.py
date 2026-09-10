import pytest
from fastmcp import FastMCP
from server import mcp


@pytest.mark.asyncio
async def test_hello_tool():
    result = await mcp.call_tool("hello", {"name": "World"})
    assert result == "Hello, World!"


@pytest.mark.asyncio
async def test_add_tool():
    result = await mcp.call_tool("add", {"a": 2, "b": 3})
    assert result == 5


@pytest.mark.asyncio
async def test_greeting_resource():
    result = await mcp.read_resource("greeting://Test")
    assert "Hello, Test!" in result
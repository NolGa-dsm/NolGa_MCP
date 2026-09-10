from fastmcp import FastMCP

mcp = FastMCP("nolga-mcp")


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


if __name__ == "__main__":
    mcp.run()
from fastmcp import FastMCP
from contextlib import asynccontextmanager
from search import search_tool

@asynccontextmanager
async def lifespan(app: FastMCP):
    yield

mcp = FastMCP("Collection Tools")

tools: list = []
for tool in tools: mcp.add_tool(tool)
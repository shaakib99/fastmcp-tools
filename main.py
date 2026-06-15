from fastmcp import FastMCP
from contextlib import asynccontextmanager
from search import search_tool
from fetch_website_content import fetch_website_content_tool
from interact_with_website import interact_with_website
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
@asynccontextmanager
async def lifespan(app: FastMCP):
    yield

mcp = FastMCP("Collection Tools")

tools: list = [search_tool, fetch_website_content_tool, interact_with_website]
for tool in tools: mcp.add_tool(tool)
from fastmcp import FastMCP
from contextlib import asynccontextmanager
from search import search_tool
from fetch_website_content import fetch_website_content_tool
from interact_with_website import interact_with_website_tool
from system_command import run_system_command_tool
from get_operating_system import get_operating_system_tool
from read_text_file import read_text_file_tool
from read_pdf_file import read_pdf_tool
from write_into_file import write_into_file_tool
from web_browser_controller import control_web_browser_tool
from find_browser_path import find_browser_path_tool
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
@asynccontextmanager
async def lifespan(app: FastMCP):
    yield

mcp = FastMCP("Collection Tools")

tools: list = [
    search_tool, 
    fetch_website_content_tool, 
    # interact_with_website_tool,
    run_system_command_tool,
    get_operating_system_tool,
    read_text_file_tool,
    read_pdf_tool,
    write_into_file_tool,
    find_browser_path_tool,
    control_web_browser_tool
    ]
for tool in tools: mcp.add_tool(tool)
import platform
from fastmcp.tools import tool

@tool
async def get_operating_system_tool():
    '''
    Use this tool to get current operating system
    '''
    return platform.system()
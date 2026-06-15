import asyncio
from fastmcp.tools import tool

@tool
async def run_system_command_tool(cmnd: str) -> tuple[int, str, str]:
    print(cmnd)
    """
        Executes a system command asynchronously in a subprocess shell.

        Suitable for:
        - Listing directory contents (ls, dir, find)
        - Reading file contents (cat, type, Get-Content)
        - Creating files and directories (mkdir, touch, New-Item)
        - Renaming files (mv, ren, Rename-Item)
        - Deleting files and directories (rm, del, Remove-Item)
        - Modifying file contents (echo, Set-Content, tee)
        - Running safe, non-destructive system commands

        Avoid using for:
        - Commands requiring elevated/root privileges
        - Network-exposed or destructive system operations
        - Long-running or blocking processes without a timeout

        Args:
            cmnd (str): The shell command to execute.

        Returns:
            tuple[int, str, str]: A tuple of:
                - return_code (int): 0 indicates success, non-zero indicates failure
                - stdout (str): Standard output from the command
                - stderr (str): Standard error output, if any
                
        Example:
            rc, out, err = await run_system_command_tool("ls -la /home/user")
            rc, out, err = await run_system_command_tool("type D:\\projects\\index.html")
        """
    # Create the subprocess and redirect standard output/error to streams
    process = await asyncio.create_subprocess_shell(
        cmnd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    # Wait for the command to finish and capture the outputs
    stdout, stderr = await process.communicate()
    
    # Decode byte outputs into readable strings
    stdout_str = stdout.decode().strip()
    stderr_str = stderr.decode().strip()
    return_code = process.returncode
    
    return return_code, stdout_str, stderr_str
from fastmcp.tools import tool
import aiofiles

@tool
async def write_into_file_tool(content: str, file_path: str) -> dict:
    """
    Asynchronously writes content to a file at the specified path.

    This tool creates or overwrites a file at the given file path with the
    provided content. If the file does not exist, it will be created. If it
    already exists, its contents will be replaced.

    Args:
        content (str): The text content to write into the file.
        file_path (str): The absolute or relative path to the target file.
                         Parent directories must already exist.

    Returns:
        dict: A result dictionary with keys:
              - "success" (bool): True if the write succeeded, False otherwise.
              - "message" (str): A human-readable status or error message.
              - "file_path" (str): The path of the file that was written.

    Raises:
        PermissionError: If the process lacks write access to the given path.
        FileNotFoundError: If any parent directory in the path does not exist.
        OSError: For other OS-level I/O failures.

    Example:
        >>> await write_into_file_tool("Hello, world!", "/tmp/hello.txt")
        {"success": True, "message": "File written successfully.", "file_path": "/tmp/hello.txt"}
    """
    try:
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write(content)
        return {
            "success": True,
            "message": "File written successfully.",
            "file_path": file_path,
        }
    except (PermissionError, FileNotFoundError, OSError) as e:
        return {
            "success": False,
            "message": f"Failed to write file: {e}",
            "file_path": file_path,
        }
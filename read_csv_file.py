from fastmcp.tools import tool

@tool
async def read_text_file_tool(file_path: str):
    """
    Read and return the full contents of a UTF-8 encoded text file.

    Args:
        file_path (str): Absolute or relative path to the text file to read.

    Returns:
        str: The complete text content of the file.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()
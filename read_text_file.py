from fastmcp.tools import tool
import os

@tool
async def read_text_file_tool(file_path: str):
    """
    - This tool can only read txt, md, html, json and basic files.
    - It can not process unstructured document like PDF, Image etc
    - Read and return the full contents of a UTF-8 encoded text file.

    Args:
        file_path (str): Absolute path to the text file to read. ex: C:/Users/test/test.txt

    Returns:
        str: The complete text content of the file.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()
            print(content)
    else:
        print(f"The file {file_path} does not exist.")
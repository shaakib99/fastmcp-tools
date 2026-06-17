from fastmcp.tools import tool
from pypdf import PdfReader

@tool
async def read_pdf_tool(local_file_path: str) -> str:
    """
    Extract and return all text content from a PDF file.

    This tool reads a PDF from the local filesystem and extracts text from every
    page, concatenating them into a single string. It also prints diagnostic info
    (total page count and first-page content) to stdout for debugging.

    Args:
        local_file_path (str): Absolute or relative path to the PDF file on the
            local filesystem. Example: "/home/user/docs/report.pdf"

    Returns:
        str: All extracted text from the PDF, with pages separated by newline
            characters. Returns an empty string if no text could be extracted
            (e.g. scanned/image-only PDFs without OCR).

    Raises:
        FileNotFoundError: If no file exists at the given path.
        pypdf.errors.PdfReadError: If the file is not a valid or readable PDF.
        PermissionError: If the process lacks read access to the file.

    Side effects:
        Prints to stdout:
            - Total number of pages in the PDF.
            - Full text content of the first page.

    Limitations:
        - Does not perform OCR; scanned or image-based PDFs will yield little
          or no text.
        - Does not preserve layout, tables, or formatting — output is plain text.
        - Large PDFs may produce very long strings; callers should handle size.

    Example:
        >>> text = await read_pdf_tool("/tmp/invoice.pdf")
        >>> print(text[:200])
        'Invoice #1234\\nDate: 2024-01-15\\nBilled to: ...'
    """
    reader = PdfReader(local_file_path)

    total_pages = len(reader.pages)
    print(f"Total Pages: {total_pages}")

    first_page = reader.pages[0]
    first_page_text = first_page.extract_text()
    print("--- First Page Content ---")
    print(first_page_text)

    all_text = ""
    for page in reader.pages:
        all_text += page.extract_text() + "\n"
    return all_text
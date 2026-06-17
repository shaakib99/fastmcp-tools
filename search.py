from crawl4ai import AsyncWebCrawler
from CONSTANT import SEARCH_ENGINE
from models import QueryParams
from fastmcp.tools import tool

@tool
async def search_tool(query: QueryParams) -> str:
    """
    Searches the internet and returns results as a markdown-formatted string.

    Use this tool when you need to:
    - Find current or real-time information
    - Research a topic, person, or event
    - Verify facts or look up unknown data
    - Lookup latest information on the internet

    Args:
        query (QueryParams): Search parameters including:
            - q        (str)           : The search query string (required)
            - limit    (int, optional) : Maximum number of results to return
            - skip     (int, optional) : Number of results to skip (for pagination)

    Returns:
        str: Search results formatted as markdown

    Example:
        result = await search_tool(QueryParams(q="latest AI news", limit=5, skip=0))
    """
    q, limit, skip = query.q, query.limit, query.skip
    url = SEARCH_ENGINE + q
    print(url)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url= url
        )
        markdown = result.markdown

        if markdown is None:
            return "No results found."
        
        # If it's an object (MarkdownGenerationResult), extract the raw string
        if hasattr(markdown, 'raw_markdown'):
            markdown = markdown.raw_markdown or "No results found."
        
        return str(markdown)  # ensure it's always a plain str
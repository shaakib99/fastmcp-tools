from crawl4ai import AsyncWebCrawler
from CONSTANT import SEARCH_ENGINE
from models import QueryParams
from fastmcp.tools import tool

@tool
async def search_tool(query: QueryParams) -> str:
    '''This tool searches the internet
        It receives QueryParams as a parameter, which includes q(Which is query itself), limit(Which is Optional) and Skip(Which is Optional)
        This tool returns the result as markdown str
    '''
    q, limit, skip = query.q, query.limit, query.skip
    url = SEARCH_ENGINE + q
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url= url
        )
        return result.markdown
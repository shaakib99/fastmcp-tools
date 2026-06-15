from crawl4ai import AsyncWebCrawler
from fastmcp.tools import tool

@tool
async def fetch_website_content_tool(url: str, content: str) -> str:
    '''This tool visit the website and fetch its contents return as markdown or clean_html format
        It receives url: str as parameter
        This tool returns the result as html or markdown str

        Example: url: test.com, content: 'markdown'
        Example #2: url: test.com, content: 'html'

        Currently only 2 content are available, ['html', 'markdown']
    '''
    print(url, content)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url= url
        )

        if content == 'html': return result.cleaned_html

        markdown = result.markdown

        if markdown is None:
            return "No results found."
        
        # If it's an object (MarkdownGenerationResult), extract the raw string
        if hasattr(markdown, 'raw_markdown'):
            markdown = markdown.raw_markdown or "No results found."
        
        return str(markdown)  # ensure it's always a plain str
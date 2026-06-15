from playwright.async_api import async_playwright
from models import WebsiteInteractionModel
from fastmcp.tools import tool
import asyncio

@tool
async def interact_with_website(data_model: WebsiteInteractionModel) -> str:
    '''
        This tool interacts with websites: fills inputs, clicks buttons.
        
        IMPORTANT: Use only simple, short CSS selectors:
        - Prefer IDs: #prompt, #sendBtn
        - Or tag+type: input[type='text'], button[type='submit']
        - NEVER use long full-path selectors like #root > div > div > input
        
        Input format:
        {
        "url": "https://example.com",
        "actions_by_sequence": [
            { "action": "fill",  "tag": "#testInput",  "value": "your text" },
            { "action": "click", "tag": "#testBtn", "value": "" }
        ]
        }
        
        Returns page content after all actions complete.
    '''
    url = data_model.url
    response = None
    async with async_playwright() as p:
        actions = data_model.actions_by_sequence
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await page.goto(url)

        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_load_state('networkidle', timeout=60000)


        for action in actions:
            try:
                if action.action == 'fill':
                    await page.wait_for_selector(action.tag, state='visible', timeout=15000)
                    await page.locator(action.tag).click()        # focus the field first
                    await page.locator(action.tag).clear()        # clear any existing value
                    await page.locator(action.tag).fill(action.value)
                    # Verify the value actually got set
                    actual = await page.locator(action.tag).input_value()
                    if actual != action.value:
                        # Fallback: type character by character like a real user
                        await page.locator(action.tag).clear()
                        await page.locator(action.tag).type(action.value, delay=50)
                elif action.action == 'click':
                    locator = page.locator(action.tag)
                    await locator.wait_for(state='visible', timeout=15000)
                    await locator.scroll_into_view_if_needed()
                    await locator.click()
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    await asyncio.sleep(30)

            except Exception as e:
                response = e.__str__()
                break 

        await page.wait_for_load_state('networkidle', timeout=30000)
        if response is None: response = await page.content()
        await page.close()
        await browser.close()
    return response
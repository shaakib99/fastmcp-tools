
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth
from agent import goal_based_agent
from fastmcp.tools import tool
from langchain.messages import HumanMessage
import uuid
import json
import asyncio

@tool
async def control_web_browser_tool(default_browser_path: str, url: str, goals: list[str]) -> str:
    """
        Use this tool whenever the user wants to DO something on a website — 
        not just find information about it.

        IMPORTANT: Before calling this tool, ALWAYS find find_browser_path by using find_browser_path_tool first
        to get the correct browser_path for this system. Never guess the path.

        Trigger this tool for requests like:
        - "play X on Y" → navigate to y and play the video
        - "search for X on Google"
        - "open ESPN and show me scores"
        - "fill out this form"
        - "log into X website"

        This tool launches a REAL Chromium browser, navigates to the URL,
        and autonomously completes the goals by clicking, typing, and interacting.

        Do NOT answer with a link. Do NOT describe what to do.
        ALWAYS call this tool when the user says "play", "open",
        "go to", "click", or "show me on" a specific website.

        Args:
            default_browser_path: executable default browser path installed in the system. figure it out by using find_browser_path_tool
            url: The full URL to navigate to (e.g. "https://www.youtube.com")
            goals: List of step-by-step goals for the agent to complete in order
                Example: ["search for argentina match highlights", "click the first video", "play the video"]
    """
    print(f'browser path: {default_browser_path}, url: {url}, goals: {goals}')
    
    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=False,
            executable_path=default_browser_path,
            args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-infobars",
            "--start-maximized",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(url)
        await page.wait_for_load_state('domcontentloaded', timeout=50000)


        system_prompt = f"""
            You are a goal-based browser agent.
            You control the browser by selecting elements and performing actions.

            Return ONLY a JSON object with this shape:
            {{
                "action": "fill | click | done | wait_for_user | return_content_to_the_parent | go_to_previous_page | close_browser | navigate | ask_user",
                "selector": "valid CSS selector string",
                "value": "text to fill (empty string if not fill)",
                "goal_completed": "description of what was just done, or null"
            }}

            Rules:
            - Use the "selector" field to target elements. Valid formats include:
            - #id        (e.g., #searchbox)
            - .class     (e.g., .gLFyf)
            - [name="x"] (e.g., [name="q"])
            - [data-ai-id="ai-N"] (fallback if no id/class is available)
            - tag[attr="val"] (e.g., textarea[name="q"])
            - For "fill", click the element first to ensure it is focused, then type.
            - If a search form has no submit button, press Enter after filling.
            - If you see a cookie consent banner, click "Reject all" or "Accept all" first.
            - If you are playing some videos or audios, send wait_for_user action to close the video by the user themselves.

            IMPORTANT RULES:
            - Once a video is PLAYING (you can see a video player on screen and timestamp is running),
            your job is DONE. Return action="wait_for_user" immediately.
            - Do NOT interact with the video player controls (progress bar, volume, etc.)
            after the video starts. This breaks the player.
            - Do NOT click anything after the video begins playing.
            - "done" = video is loaded and playing. Return action="wait_for_user" at that point.

            Goals to complete IN ORDER: {json.dumps(goals)}
            """
        agent = await goal_based_agent(system_prompt)
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        completed_goals = []
        last_result = "No actions taken yet."
        error_streak = 0

        while True:
            snapshot = await get_labeled_snapshot(page)

            message = f"""
                <last_action_result>{last_result}</last_action_result>
                <current_url>{page.url}</current_url>
                <completed_goals>{json.dumps(completed_goals)}</completed_goals>
                <remaining_goals>{json.dumps([g for g in goals if g not in completed_goals])}</remaining_goals>
                <page_elements>{json.dumps(snapshot, indent=2)}</page_elements>
            """
            response = await agent.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=config
            )

            try:
                goal = json.loads(response["messages"][-1].content)
                action   = goal["action"]
                selector = goal.get("selector", "")
                value    = goal.get("value", "")
                goal_completed = goal.get("goal_completed")

                print(f'[AGENT] action={action}, selector={selector}, value={value[:50]}, goal={goal_completed}')

                if action == "fill":
                    await page.wait_for_selector(selector, state="visible", timeout=10000)
                    # Click first to focus (critical for Google, React, etc.)
                    await page.locator(selector).click()
                    await page.locator(selector).fill(value)
                    # Press Enter in case there's no explicit search button
                    await page.locator(selector).press("Enter")
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    last_result = f"Filled {selector} with '{value}' and pressed Enter"

                elif action == "click":
                    await page.wait_for_selector(selector, state="visible", timeout=50000)
                    await page.locator(selector).click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    last_result = f"Clicked {selector}"

                elif action == "done":
                    # await context.close()
                    # await browser.close()
                    return f"All goals completed, Browser kept open:  {json.dumps(completed_goals)}"

                elif action == "wait_for_user":
                    return f'Handing over the control to the user'

                elif action == "return_content_to_the_parent":
                    content = await page.content()
                    await page.close()
                    await browser.close()
                    return content

                elif action == "go_to_previous_page":
                    await page.go_back()
                    await page.wait_for_load_state('domcontentloaded', timeout=30000)

                    last_result = f'Went back to previous page'
                
                elif action == "navigate":
                    await page.goto(value)
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    last_result = f"Navigated to {value}"

                elif action == "close_browser":
                    await browser.close()
                    return "Browser closed by agent."
                
                elif action == "ask_user":
                    return f'''
                        Needs Input From User: 
                            <context>
                                <url>{url}</url>
                                <page_content>{await page.content()}</page_content>
                        Ask {value}'''

                else:
                    last_result = f'Unrecognized action: {action}'

                if goal_completed and goal_completed not in completed_goals:
                    completed_goals.append(goal_completed)

                error_streak = 0

            except Exception as e:
                error_streak += 1
                last_result = f"Error: {e}"
                print(f"[ERROR] {e}")
                if error_streak >= 3:
                    await browser.close()
                    return f"Aborting after 3 consecutive errors. Last: {e}"
    except Exception as e:
        return f'''Exception occured: {e.__str__()}\n What to do? DONOT REOPEN THE BROWSER. Explain what happened to the user.'''


async def get_labeled_snapshot(page) -> list[dict]:
    await page.evaluate("""() => {
        let idx = 0;
        document.querySelectorAll(
            'a, button, input, select, textarea, video, ' +
            '[onclick], [role="button"], [role="link"], ' +
            '[role="searchbox"], [role="combobox"], ' +
            '[role="menuitem"], [tabindex]:not([tabindex="-1"])'
        ).forEach(el => {
            el.setAttribute('data-ai-id', `ai-${idx++}`);
        });
    }""")

    snapshot = await page.evaluate("""() => {
        function isVisible(el) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
            // Don't use offsetParent — it fails for fixed/sticky elements
            return rect.top < window.innerHeight && rect.bottom > 0;
        }

        function getLabel(el) {
            // Try multiple strategies to get a meaningful label
            return (
                el.getAttribute('aria-label') ||
                el.getAttribute('title') ||
                el.getAttribute('alt') ||
                el.labels?.[0]?.innerText?.trim() ||
                el.placeholder ||
                el.getAttribute('name') ||
                el.innerText?.trim().slice(0, 80) ||
                el.getAttribute('data-tooltip') ||
                null
            );
        }

        return [...document.querySelectorAll('[data-ai-id]')]
            .filter(isVisible)
            .map(el => ({
                id: el.getAttribute('data-ai-id'),
                tag: el.tagName.toLowerCase(),
                type: el.type || el.getAttribute('role') || null,
                name: el.name || null,
                label: getLabel(el),
                href: el.href || el.closest('a')?.href || null,
                selector: el.id
                    ? `#${el.id}`
                    : el.getAttribute('name')
                        ? `[name="${el.getAttribute('name')}"]`
                        : `[data-ai-id="${el.getAttribute('data-ai-id')}"]`
            }));
    }""")

    return snapshot
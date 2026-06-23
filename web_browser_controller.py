from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth.stealth import Stealth
from agent import goal_based_agent
from fastmcp.tools import tool
from langchain.messages import HumanMessage
import uuid
import json
import asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def wait_for_page_settled(page, timeout: int = 30_000) -> None:
    """
    Wait until the page is truly idle:
      1. domcontentloaded  – HTML parsed
      2. networkidle       – no in-flight requests for 500 ms (best-effort)
      3. DOM stability     – no new nodes added for 300 ms
    Each step gracefully degrades on timeout so we never hard-crash here.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except PlaywrightTimeoutError:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout, 10_000))
    except PlaywrightTimeoutError:
        pass

    try:
        await page.evaluate("""() => new Promise((resolve) => {
            let lastCount = -1;
            let stable = 0;
            const check = () => {
                const count = document.querySelectorAll('*').length;
                if (count === lastCount) {
                    stable++;
                    if (stable >= 3) return resolve();
                } else {
                    stable = 0;
                    lastCount = count;
                }
                setTimeout(check, 100);
            };
            check();
        })""")
    except Exception:
        pass


async def wait_for_dom_mutation(page, timeout: int = 8_000) -> bool:
    """
    After a dropdown selection, the page may re-render new fields via JS.
    This waits for ANY DOM mutation to occur within `timeout` ms, then
    waits for the DOM to fully settle before returning.
    """
    try:
        mutated = await page.evaluate(f"""() => new Promise((resolve) => {{
            let resolved = false;
            const observer = new MutationObserver((mutations) => {{
                if (!resolved) {{
                    resolved = true;
                    observer.disconnect();
                    resolve(true);
                }}
            }});
            observer.observe(document.body, {{
                childList: true,
                subtree: true,
                attributes: true,
                characterData: false
            }});
            setTimeout(() => {{
                if (!resolved) {{
                    resolved = true;
                    observer.disconnect();
                    resolve(false);
                }}
            }}, {timeout});
        }})""")
        if mutated:
            await wait_for_page_settled(page, timeout=15_000)
        return mutated
    except Exception:
        return False


async def scroll_into_view(page, selector: str) -> None:
    """Scroll the element into the viewport before interacting."""
    try:
        await page.locator(selector).scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass


async def get_all_inputs(page) -> list[dict]:
    """
    Return EVERY input/select/textarea in the DOM regardless of visibility.
    """
    return await page.evaluate(r"""() => {
        function bestSelector(el) {
            if (el.id) {
                return /[.:#\[\]()~+>]/.test(el.id)
                    ? `[id="${el.id}"]`
                    : '#' + el.id;
            }
            if (el.name) return `[name="${el.name}"]`;
            let parent = el.parentElement;
            while (parent && parent !== document.body) {
                if (parent.id) {
                    const tag = el.tagName.toLowerCase();
                    const idx = [...parent.querySelectorAll(tag)].indexOf(el);
                    return `#${parent.id} ${tag}:nth-of-type(${idx + 1})`;
                }
                parent = parent.parentElement;
            }
            return el.tagName.toLowerCase();
        }

        function isVisible(el) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return false;
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }

        return [...document.querySelectorAll('input, select, textarea')].map(el => ({
            selector:    bestSelector(el),
            tag:         el.tagName.toLowerCase(),
            type:        el.type || null,
            id:          el.id || null,
            name:        el.name || null,
            placeholder: el.placeholder || null,
            label:       el.labels?.[0]?.innerText?.trim() ||
                         el.getAttribute('aria-label') || null,
            value:       el.value || null,
            visible:     isVisible(el),
            disabled:    el.disabled,
            required:    el.required || false,
            file_input:  el.type === 'file',
        }));
    }""")


async def get_form_errors(page) -> list[dict]:
    """
    Scrape ALL validation errors from the page.
    """
    return await page.evaluate(r"""() => {
        const errors = [];
        const seen = new Set();

        function bestSelector(el) {
            if (el.id) return /[.:#\[\]()~+>]/.test(el.id)
                ? `[id="${el.id}"]` : `#${el.id}`;
            if (el.name) return `[name="${el.name}"]`;
            return el.tagName.toLowerCase();
        }

        function addError(selector, label, msg) {
            const key = `${selector}::${msg}`;
            if (msg && !seen.has(key)) {
                seen.add(key);
                errors.push({ selector, label, error: msg.trim() });
            }
        }

        document.querySelectorAll('[aria-invalid="true"]').forEach(field => {
            const selector = bestSelector(field);
            const label = field.getAttribute('aria-label')
                || field.placeholder || field.name || field.id || null;

            const describedBy = field.getAttribute('aria-describedby');
            if (describedBy) {
                describedBy.split(/\s+/).forEach(id => {
                    const el = document.getElementById(id);
                    const txt = el?.innerText?.trim();
                    if (txt) addError(selector, label, txt);
                });
            }

            let ancestor = field.parentElement;
            let depth = 0;
            while (ancestor && depth < 5) {
                ancestor.querySelectorAll('*').forEach(child => {
                    if (child === field) return;
                    const cls = (child.className || '').toLowerCase();
                    const role = (child.getAttribute('role') || '').toLowerCase();
                    const isErrorNode =
                        /error|invalid|danger|feedback|alert|warn|help/.test(cls) ||
                        role === 'alert' || role === 'status';
                    if (isErrorNode) {
                        const txt = child.innerText?.trim();
                        if (txt) addError(selector, label, txt);
                    }
                });
                if (errors.find(e => e.selector === selector)) break;
                ancestor = ancestor.parentElement;
                depth++;
            }
        });

        const errorQuery = [
            '[role="alert"]', '[role="status"]', '[aria-live="polite"]',
            '[aria-live="assertive"]', '[class*="error"]', '[class*="invalid"]',
            '[class*="danger"]', '[class*="feedback"]', '[class*="warning"]',
        ].join(', ');

        document.querySelectorAll(errorQuery).forEach(el => {
            const txt = el.innerText?.trim();
            if (!txt || txt.length > 300) return;
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                addError(null, 'page-level', txt);
            }
        });

        return errors;
    }""")


async def get_labeled_snapshot(page) -> list[dict]:
    """Label every interactive element with data-ai-id and return a snapshot."""
    await page.evaluate("""() => {
        let idx = 0;
        document.querySelectorAll(
            'a, button, input, select, textarea, video, option, ' +
            '[onclick], [role="button"], [role="link"], ' +
            '[role="option"], [role="listbox"], [role="combobox"], ' +
            '[role="searchbox"], [role="menuitem"], ' +
            '[tabindex]:not([tabindex="-1"])'
        ).forEach(el => {
            if (!el.hasAttribute('data-ai-id'))
                el.setAttribute('data-ai-id', `ai-${idx++}`);
        });
    }""")

    snapshot = await page.evaluate(r"""() => {
        function isVisible(el) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
            return rect.top < window.innerHeight && rect.bottom > 0;
        }

        function getLabel(el) {
            return (
                el.getAttribute('aria-label') ||
                el.getAttribute('title') ||
                el.getAttribute('alt') ||
                el.labels?.[0]?.innerText?.trim() ||
                el.placeholder ||
                el.getAttribute('name') ||
                el.innerText?.trim().slice(0, 80) ||
                el.getAttribute('data-tooltip') ||
                el.value || null
            );
        }

        return [...document.querySelectorAll('[data-ai-id]')]
            .filter(isVisible)
            .map(el => ({
                id:       el.getAttribute('data-ai-id'),
                tag:      el.tagName.toLowerCase(),
                type:     el.type || el.getAttribute('role') || null,
                name:     el.name || null,
                label:    getLabel(el),
                value:    el.value ?? null,
                checked:  el.type === 'checkbox' || el.type === 'radio'
                            ? el.checked : null,
                options:  el.tagName === 'SELECT'
                            ? [...el.options].map(o => ({ value: o.value, text: o.text }))
                            : null,
                href:     el.href || el.closest('a')?.href || null,
                error:    (() => {
                    if (el.getAttribute('aria-invalid') !== 'true') return null;
                    const desc = el.getAttribute('aria-describedby');
                    if (desc) {
                        const msgs = desc.split(/\s+/)
                            .map(id => document.getElementById(id)?.innerText?.trim())
                            .filter(Boolean);
                        if (msgs.length) return msgs.join(' | ');
                    }
                    let ancestor = el.parentElement, depth = 0;
                    while (ancestor && depth < 5) {
                        for (const child of ancestor.querySelectorAll('*')) {
                            if (child === el) continue;
                            const cls = (child.className || '').toLowerCase();
                            const role = (child.getAttribute('role') || '').toLowerCase();
                            if (/error|invalid|danger|feedback|alert|warn|help/.test(cls)
                                    || role === 'alert' || role === 'status') {
                                const txt = child.innerText?.trim();
                                if (txt) return txt;
                            }
                        }
                        ancestor = ancestor.parentElement;
                        depth++;
                    }
                    return 'invalid (no message found)';
                })(),
                selector: el.id
                    ? (/[.:#\[\]()~+>]/.test(el.id) ? `[id="${el.id}"]` : `#${el.id}`)
                    : el.getAttribute('name')
                        ? `[name="${el.getAttribute('name')}"]`
                        : `[data-ai-id="${el.getAttribute('data-ai-id')}"]`
            }));
    }""")

    return snapshot


def _clean_json(raw: str) -> str:
    """Strip markdown fences and whitespace from LLM output."""
    if not raw:
        return ""
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@tool
async def control_web_browser_tool(default_browser_path: str, url: str, context_for_agent: str, goals: list[str]) -> str:
    """
    Use this tool whenever the user wants to DO something on a website.
    """
    print(f'browser_path={default_browser_path}, url={url}, context_for_agent={context_for_agent} goals={goals}')

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
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(url)
        await wait_for_page_settled(page)

        system_prompt = f"""
You are a goal-based browser agent. You control the browser by selecting elements and performing actions.
Context from the parent: 
<context>{context_for_agent}</context>

Return ONLY a valid JSON object — no markdown, no explanation — with this exact shape:
{{
    "action":         "fill | type | click | check | uncheck | upload_file | select | hover | scroll | wait_for_page | wait_for_element | inspect_page | done | wait_for_user | return_content_to_the_parent | go_to_previous_page | close_browser | navigate | ask_user",
    "selector":       "valid CSS selector string (empty string when not needed)",
    "value":          "text to fill / option value to select / URL to navigate to (empty string if not needed)",
    "goal_completed": "description of what was just accomplished, or null"
}}

--- ACTION REFERENCE ---

fill      – Clear an <input> or <textarea> and type new text. Does NOT press Enter.
            Use for ALL text fields, search boxes, email/password inputs, textareas.
            NEVER press Enter after fill — let the agent handle submission separately.

type      – Type text character-by-character WITHOUT clearing first (for auto-complete
            inputs where clearing breaks suggestions). Does NOT press Enter.

click     – Click any element (buttons, links, custom dropdown triggers, submit buttons).
            NEVER use click on checkboxes or radio buttons — use check/uncheck instead.
            After clicking a submit/next/continue button, last_action_result will include
            any validation errors found. Fix ALL errors before clicking submit again.

upload_file – Upload a file to an <input type="file"> element.
            "selector" targets the file input (often hidden behind a styled button).
            "value" is the absolute path to the file on disk.
            Do NOT click the upload button first; set_input_files bypasses the dialog.

check     – Set a checkbox or radio to CHECKED. Safe to call even if already checked.
            Always use this instead of click for checkboxes/radios.

uncheck   – Set a checkbox to UNCHECKED. Safe to call even if already unchecked.

select    – Set the value of a NATIVE <select> element directly.
            "value" must be the <option> value attribute (not display text).
            If the options list shows value="" use the visible text instead.
            After selecting, the tool detects if the page re-rendered new fields.

inspect_page – Dump ALL input/select/textarea elements in the DOM (including hidden ones)
            with their real selectors, labels, current values, and visibility flags.
            Use this whenever wait_for_element times out or you are unsure what
            selector to use. "selector" and "value" can be empty.

wait_for_element – Wait until a specific selector becomes visible before continuing.
            Use this AFTER a select/click that triggers conditional content.
            IMPORTANT: always use wait_for_element before trying to fill/click any element
            that did not exist before the last select or click action.

hover     – Hover over an element to reveal hidden menus or tooltips before clicking.

scroll    – Scroll the page. "value" should be "up" | "down" | a pixel offset like "500".

wait_for_page – Explicitly wait for the page to finish loading.

navigate  – Go to a new URL. Put the full URL in "value".

go_to_previous_page – Press the browser Back button.

done      – All goals are complete. Return a summary.

wait_for_user – Hand control to the user (e.g. CAPTCHA, 2FA, video playing).

return_content_to_the_parent – Return the raw HTML of the current page, then close.

close_browser – Close the browser session.

ask_user  – ONLY for information the page itself cannot provide:
            credentials, personal data, decisions. 
            NEVER use this to ask about UI elements, selectors, or button labels.

--- FORM FILLING STRATEGY (CRITICAL) ---
When filling out a form with multiple fields:
  1. First, use inspect_page to see ALL fields and their current values.
  2. Fill each field ONE AT A TIME using action="fill".
  3. NEVER press Enter after filling a field — only the final submit should trigger submission.
  4. For dropdowns (<select>), use action="select" with the option VALUE (not text).
  5. After each select, read last_action_result carefully:
     - "Page re-rendered" → new fields appeared. Use inspect_page to see them.
     - "No DOM change" → the select worked but no new fields appeared.
     - "dropdown value did NOT change" → the select failed. Retry or try label instead.
  6. For checkboxes/radios, use check/uncheck (NEVER click).
  7. Only click the submit button AFTER all required fields are filled.
  8. If validation errors appear after submit, fix EVERY error field, then submit again.
  9. If you don't find fields in the form that is in the context, skip it. Do not try to search it.

--- GENERAL RULES ---
- NEVER use ask_user to request a CSS selector, button label, or any UI detail.
  If you cannot find a button or field, use inspect_page.
- Before clicking Next/Submit/Continue: if you don't see it in <page_elements>,
  run inspect_page and look for visible buttons with text like "next", "submit", "continue".
- If you see a cookie/consent banner, dismiss it FIRST (click "Reject all" or "Accept all").
- Before interacting with any element, scroll it into view.
- After any action that triggers navigation or significant DOM change, use wait_for_page.
- For NATIVE dropdowns (<select>): use action="select".
- For CUSTOM dropdowns (div/ul based): use action="click" to open, then click the option.

- If wait_for_element times out even ONCE:
    → Immediately use inspect_page before any other action.
    → Use the exact selector from inspect_page output.
    → NEVER retry wait_for_element with the same selector that already timed out.

- If the same action+selector repeats without progress → use inspect_page.
- Once a video is PLAYING, immediately return action="wait_for_user".
- If the same error repeats twice, try an alternative selector or strategy.

Goals to complete IN ORDER:
{json.dumps(goals)}
"""

        agent = await goal_based_agent(system_prompt)
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        completed_goals: list[str] = []
        last_result = "No actions taken yet."
        error_streak = 0

        LOOP_THRESHOLD = 3
        recent_actions: list[tuple] = []

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
                config=config,
            )

            try:
                raw = response["messages"][-1].content or ""
                cleaned = _clean_json(raw)

                if not cleaned:
                    raise ValueError("Empty response from agent")

                goal = json.loads(cleaned)

                if "action" not in goal:
                    raise ValueError(f"Missing 'action' key. Parsed: {cleaned[:200]}")

                action = goal["action"]
                selector = goal.get("selector", "")
                value = goal.get("value", "")
                goal_completed = goal.get("goal_completed")

                print(
                    f"[AGENT] action={action} | selector={selector} "
                    f"| value={str(value)[:60]} | goal={goal_completed}"
                )

                # ── Loop-guard check ─────────────────────────────────────────
                key = (action, selector, value)
                recent_actions.append(key)
                if len(recent_actions) > LOOP_THRESHOLD:
                    recent_actions.pop(0)

                if (
                    len(recent_actions) == LOOP_THRESHOLD
                    and len(set(recent_actions)) == 1
                ):
                    all_inputs = await get_all_inputs(page)
                    current_value = None
                    if selector:
                        try:
                            current_value = await page.evaluate(
                                f"(sel) => {{ const el = document.querySelector(sel); return el ? el.value : null; }}",
                                selector
                            )
                        except Exception:
                            pass

                    last_result = (
                        f"⚠️ LOOP DETECTED — same action '{action}' on '{selector}' "
                        f"repeated {LOOP_THRESHOLD} times with no progress.\n"
                        f"Current value of '{selector}': {current_value!r}\n"
                        f"ALL inputs/selects/textareas in DOM right now:\n"
                        f"{json.dumps(all_inputs, indent=2)}\n"
                        "INSTRUCTIONS: Stop retrying this action. Use inspect_page to verify "
                        "the current page state, then pick the correct selector from the list above."
                    )
                    recent_actions.clear()
                    print(f"[LOOP-GUARD] Diagnostic injected. Selectors: {[i['selector'] for i in all_inputs]}")
                    continue

                # ── Actions ──────────────────────────────────────────────────

                if action == "fill":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)
                    await page.locator(selector).click()
                    await page.locator(selector).fill(value)
                    # DO NOT press Enter — let the agent control submission
                    await wait_for_page_settled(page)
                    await page.locator(selector).blur()

                    last_result = f"Filled '{selector}' with '{value}'"

                elif action == "type":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)
                    await page.locator(selector).click()
                    await page.locator(selector).type(value, delay=50)
                    last_result = f"Typed '{value}' into '{selector}'"

                elif action == "check":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)
                    is_checked = await page.locator(selector).is_checked()
                    if not is_checked:
                        await page.locator(selector).click()
                    await wait_for_dom_mutation(page, timeout=3_000)
                    last_result = f"Checked '{selector}' (was_checked={is_checked})"

                elif action == "uncheck":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)
                    is_checked = await page.locator(selector).is_checked()
                    if is_checked:
                        await page.locator(selector).click()
                    await wait_for_dom_mutation(page, timeout=3_000)
                    last_result = f"Unchecked '{selector}' (was_checked={is_checked})"

                elif action == "upload_file":
                    await page.wait_for_selector(selector, state='attached', timeout=15_000)
                    await page.locator(selector).set_input_files(value)
                    await wait_for_dom_mutation(page, timeout=5_000)
                    last_result = f"Uploaded file '{value}' to '{selector}'"

                elif action == "click":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)

                    # FIXED: Use proper string formatting instead of broken f-string with escaped braces
                    selector_json = json.dumps(selector)
                    el_type = await page.evaluate(
                        f"(sel) => {{ const el = document.querySelector(sel); return el ? (el.type || el.tagName.toLowerCase()) : null; }}",
                        selector
                    )
                    el_text = await page.evaluate(
                        f"(sel) => {{ const el = document.querySelector(sel); return el ? (el.innerText || el.value || '').trim().toLowerCase() : ''; }}",
                        selector
                    )
                    is_submit = (
                        el_type in ("submit",)
                        or any(w in el_text for w in ("next", "submit", "continue", "proceed", "apply", "save"))
                    )

                    await page.locator(selector).click()
                    await wait_for_page_settled(page)

                    if is_submit:
                        errors = await get_form_errors(page)
                        if errors:
                            last_result = (
                                f"Clicked '{selector}' (submit). "
                                f"⚠️ FORM VALIDATION ERRORS DETECTED — do NOT move to next goal.\n"
                                f"Fix ALL errors below, then click submit again:\n"
                                f"{json.dumps(errors, indent=2)}\n"
                                "NEXT STEPS: (1) fix each field using its selector, "
                                "(2) once all fixed, click submit again, "
                                "(3) only proceed when no errors appear."
                            )
                            print(f"[SUBMIT] Errors found: {errors}")
                        else:
                            last_result = f"Clicked '{selector}' (submit) — no validation errors"
                    else:
                        last_result = f"Clicked '{selector}'"

                elif action == "select":
                    await page.wait_for_selector(selector, state="visible", timeout=15_000)
                    await scroll_into_view(page, selector)

                    value_before = await page.evaluate(
                        f"(sel) => {{ const el = document.querySelector(sel); return el?.value ?? null; }}",
                        selector
                    )

                    try:
                        selected = await page.locator(selector).select_option(value=value)
                        if not selected:
                            raise ValueError("No option matched by value")
                        last_result = f"Selected option value='{value}' in '{selector}'"
                    except Exception:
                        await page.locator(selector).select_option(label=value)
                        last_result = f"Selected option label='{value}' in '{selector}'"

                    value_after = await page.evaluate(
                        f"(sel) => {{ const el = document.querySelector(sel); return el?.value ?? null; }}",
                        selector
                    )

                    if value_before == value_after:
                        last_result += (
                            f" | ⚠️ WARNING: dropdown value did NOT change "
                            f"(still '{value_after}'). Option may be disabled or JS-controlled."
                        )
                    else:
                        mutated = await wait_for_dom_mutation(page, timeout=8_000)
                        if mutated:
                            last_result += (
                                " | ✅ Page re-rendered after selection — "
                                "new elements available. Check <page_elements> before next action."
                            )
                        else:
                            last_result += " | No DOM change detected after selection"

                elif action == "wait_for_element":
                    try:
                        await page.wait_for_selector(selector, state="visible", timeout=15_000)
                        await wait_for_page_settled(page, timeout=10_000)
                        last_result = f"✅ Element '{selector}' is now visible"
                    except PlaywrightTimeoutError:
                        all_inputs = await get_all_inputs(page)
                        last_result = (
                            f"❌ Timeout: '{selector}' did not appear within 15s.\n"
                            f"ALL inputs in DOM:\n{json.dumps(all_inputs, indent=2)}\n"
                            "1. Do NOT retry same selector.\n"
                            "2. Find correct selector from list above.\n"
                            "3. If field visible=false, re-trigger the parent dropdown first."
                        )

                elif action == "inspect_page":
                    all_inputs = await get_all_inputs(page)
                    all_buttons = await page.evaluate(r"""() => {
                        function bestSelector(el) {
                            if (el.id) return /[.:#\[\]()~+>]/.test(el.id)
                                ? `[id="${el.id}"]` : '#' + el.id;
                            if (el.name) return `[name="${el.name}"]`;
                            const ai = el.getAttribute('data-ai-id');
                            if (ai) return `[data-ai-id="${ai}"]`;
                            return el.tagName.toLowerCase();
                        }
                        function isVisible(el) {
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 || r.height === 0) return false;
                            const s = window.getComputedStyle(el);
                            return s.display !== 'none' && s.visibility !== 'hidden';
                        }
                        const els = [
                            ...document.querySelectorAll(
                                'button, input[type=submit], input[type=button], ' +
                                '[role=button], a[href]'
                            )
                        ];
                        return els.map(el => ({
                            selector: bestSelector(el),
                            tag:      el.tagName.toLowerCase(),
                            type:     el.type || el.getAttribute('role') || null,
                            text:     (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                            visible:  isVisible(el),
                            disabled: el.disabled || false,
                        }));
                    }""")
                    last_result = (
                        f"inspect_page result:\n"
                        f"FORM FIELDS:\n{json.dumps(all_inputs, indent=2)}\n\n"
                        f"BUTTONS/LINKS:\n{json.dumps(all_buttons, indent=2)}\n"
                        "Use 'selector' from this list. Check 'visible' and 'disabled' before clicking."
                    )

                elif action == "hover":
                    await page.wait_for_selector(selector, state="visible", timeout=10_000)
                    await scroll_into_view(page, selector)
                    await page.locator(selector).hover()
                    await asyncio.sleep(0.4)
                    last_result = f"Hovered over '{selector}'"

                elif action == "scroll":
                    if value == "up":
                        await page.keyboard.press("Home")
                    elif value == "down":
                        await page.keyboard.press("End")
                    else:
                        try:
                            pixels = int(value)
                            await page.evaluate(f"window.scrollBy(0, {pixels})")
                        except ValueError:
                            await page.evaluate("window.scrollBy(0, 600)")
                    await asyncio.sleep(0.3)
                    last_result = f"Scrolled '{value}'"

                elif action == "wait_for_page":
                    await wait_for_page_settled(page, timeout=30_000)
                    last_result = "Waited for page to fully load"

                elif action == "navigate":
                    await page.goto(value)
                    await wait_for_page_settled(page)
                    last_result = f"Navigated to '{value}'"

                elif action == "go_to_previous_page":
                    await page.go_back()
                    await wait_for_page_settled(page)
                    last_result = "Went back to previous page"

                elif action == "done":
                    return f"All goals completed: {json.dumps(completed_goals)}"

                elif action == "wait_for_user":
                    await browser.close()
                    return "Handing control over to the user."

                elif action == "return_content_to_the_parent":
                    content = await page.content()
                    await browser.close()
                    return content

                elif action == "close_browser":
                    await browser.close()
                    return "Browser closed by agent."

                elif action == "ask_user":
                    await browser.close()
                    return (
                        f"Needs input from user:\n"
                        f"<context><url>{url}</url></context>\n"
                        f"Question: {value}"
                    )

                else:
                    last_result = f"Unrecognized action: '{action}'"

                if goal_completed and goal_completed not in completed_goals:
                    completed_goals.append(goal_completed)

                error_streak = 0

            except Exception as e:
                error_streak += 1
                last_result = f"Error: {e}"
                print(f"[ERROR] {e}")
                if error_streak >= 3:
                    await browser.close()
                    return f"Aborting after 3 consecutive errors. Last error: {e}"

    except Exception as e:
        try:
            await browser.close()
        except Exception:
            pass
        return (
            f"Exception occurred: {e}\n"
            "DO NOT reopen the browser. Please explain what happened to the user."
        )
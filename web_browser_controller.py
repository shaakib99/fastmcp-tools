from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth.stealth import Stealth
from agent import goal_based_agent
from fastmcp.tools import tool
from langchain.messages import HumanMessage
import uuid
import json
import ast
import asyncio


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

async def wait_for_page_settled(page, timeout: int = 30_000) -> None:
    """Wait until the page is truly idle: DOM parsed → network idle → DOM stable."""
    for state in ("domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(
                state, timeout=timeout if state == "domcontentloaded" else min(timeout, 10_000)
            )
        except PlaywrightTimeoutError:
            pass
    try:
        await page.evaluate("""() => new Promise((resolve) => {
            let lastCount = -1, stable = 0;
            const check = () => {
                const count = document.querySelectorAll('*').length;
                stable = count === lastCount ? stable + 1 : 0;
                lastCount = count;
                if (stable >= 3) return resolve();
                setTimeout(check, 100);
            };
            check();
        })""")
    except Exception:
        pass


async def wait_for_dom_mutation(page, timeout: int = 8_000) -> bool:
    """Wait for any DOM mutation within timeout ms, then let page settle."""
    try:
        mutated = await page.evaluate(f"""() => new Promise((resolve) => {{
            let resolved = false;
            const observer = new MutationObserver(() => {{
                if (!resolved) {{ resolved = true; observer.disconnect(); resolve(true); }}
            }});
            observer.observe(document.body, {{
                childList: true, subtree: true, attributes: true, characterData: false
            }});
            setTimeout(() => {{
                if (!resolved) {{ resolved = true; observer.disconnect(); resolve(false); }}
            }}, {timeout});
        }})""")
        if mutated:
            await wait_for_page_settled(page, timeout=15_000)
        return mutated
    except Exception:
        return False


async def scroll_into_view(page, selector: str) -> None:
    try:
        await page.locator(selector).scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass


async def get_all_inputs(page) -> list[dict]:
    """Return every input/select/textarea in the DOM regardless of visibility."""
    return await page.evaluate(r"""() => {
        function bestSelector(el) {
            if (el.id) return /[.:#\[\]()~+>]/.test(el.id) ? `[id="${el.id}"]` : '#' + el.id;
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
            label:       el.labels?.[0]?.innerText?.trim() || el.getAttribute('aria-label') || null,
            value:       el.value || null,
            visible:     isVisible(el),
            disabled:    el.disabled,
            required:    el.required || false,
            file_input:  el.type === 'file',
        }));
    }""")


async def get_form_errors(page) -> list[dict]:
    """Scrape all visible validation errors from the page."""
    return await page.evaluate(r"""() => {
        const errors = [], seen = new Set();
        function bestSelector(el) {
            if (el.id) return /[.:#\[\]()~+>]/.test(el.id) ? `[id="${el.id}"]` : `#${el.id}`;
            if (el.name) return `[name="${el.name}"]`;
            return el.tagName.toLowerCase();
        }
        function addError(selector, label, msg) {
            const key = `${selector}::${msg}`;
            if (msg && !seen.has(key)) { seen.add(key); errors.push({ selector, label, error: msg.trim() }); }
        }
        document.querySelectorAll('[aria-invalid="true"]').forEach(field => {
            const selector = bestSelector(field);
            const label = field.getAttribute('aria-label') || field.placeholder || field.name || field.id || null;
            const describedBy = field.getAttribute('aria-describedby');
            if (describedBy) {
                describedBy.split(/\s+/).forEach(id => {
                    const txt = document.getElementById(id)?.innerText?.trim();
                    if (txt) addError(selector, label, txt);
                });
            }
            let ancestor = field.parentElement, depth = 0;
            while (ancestor && depth < 5) {
                ancestor.querySelectorAll('*').forEach(child => {
                    if (child === field) return;
                    const cls = (child.className || '').toLowerCase();
                    const role = (child.getAttribute('role') || '').toLowerCase();
                    if (/error|invalid|danger|feedback|alert|warn|help/.test(cls) || role === 'alert' || role === 'status') {
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
            '[role="alert"]', '[role="status"]', '[aria-live="polite"]', '[aria-live="assertive"]',
            '[class*="error"]', '[class*="invalid"]', '[class*="danger"]', '[class*="feedback"]', '[class*="warning"]',
        ].join(', ');
        document.querySelectorAll(errorQuery).forEach(el => {
            const txt = el.innerText?.trim();
            if (!txt || txt.length > 300) return;
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) addError(null, 'page-level', txt);
        });
        return errors;
    }""")


async def get_labeled_snapshot(page) -> list[dict]:
    """Label every interactive element with data-ai-id and return a snapshot."""
    await page.evaluate("""() => {
        let idx = 0;
        document.querySelectorAll(
            'a, button, input, select, textarea, video, option, ' +
            '[onclick], [role="button"], [role="link"], [role="option"], [role="listbox"], ' +
            '[role="combobox"], [role="searchbox"], [role="menuitem"], [tabindex]:not([tabindex="-1"])'
        ).forEach(el => { if (!el.hasAttribute('data-ai-id')) el.setAttribute('data-ai-id', `ai-${idx++}`); });
    }""")
    return await page.evaluate(r"""() => {
        function isVisible(el) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
            return rect.top < window.innerHeight && rect.bottom > 0;
        }
        function getLabel(el) {
            return (
                el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt') ||
                el.labels?.[0]?.innerText?.trim() || el.placeholder || el.getAttribute('name') ||
                el.innerText?.trim().slice(0, 80) || el.getAttribute('data-tooltip') || el.value || null
            );
        }
        function getSelector(el) {
            if (el.id) return /[.:#\[\]()~+>]/.test(el.id) ? `[id="${el.id}"]` : `#${el.id}`;
            if (el.name) return `[name="${el.getAttribute('name')}"]`;
            return `[data-ai-id="${el.getAttribute('data-ai-id')}"]`;
        }
        function getError(el) {
            if (el.getAttribute('aria-invalid') !== 'true') return null;
            const desc = el.getAttribute('aria-describedby');
            if (desc) {
                const msgs = desc.split(/\s+/).map(id => document.getElementById(id)?.innerText?.trim()).filter(Boolean);
                if (msgs.length) return msgs.join(' | ');
            }
            let ancestor = el.parentElement, depth = 0;
            while (ancestor && depth < 5) {
                for (const child of ancestor.querySelectorAll('*')) {
                    if (child === el) continue;
                    const cls = (child.className || '').toLowerCase();
                    const role = (child.getAttribute('role') || '').toLowerCase();
                    if (/error|invalid|danger|feedback|alert|warn|help/.test(cls) || role === 'alert' || role === 'status') {
                        const txt = child.innerText?.trim();
                        if (txt) return txt;
                    }
                }
                ancestor = ancestor.parentElement;
                depth++;
            }
            return 'invalid (no message found)';
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
                checked:  (el.type === 'checkbox' || el.type === 'radio') ? el.checked : null,
                options:  el.tagName === 'SELECT' ? [...el.options].map(o => ({ value: o.value, text: o.text })) : null,
                href:     el.href || el.closest('a')?.href || null,
                error:    getError(el),
                selector: getSelector(el),
            }));
    }""")


def _clean_json(raw: str) -> str:
    """Strip markdown fences and whitespace from LLM output."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_list_value(value) -> list:
    """Parse a value that should be a list — handles JSON arrays and Python-style lists."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    raise ValueError(f"Cannot parse list from type {type(value)}")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

def _sub_agent_system_prompt(goal: str, context_for_agent: str) -> str:
    return f"""
You are a focused browser sub-agent. You have ONE job: complete the goal below and verify it.
Do not attempt anything outside this goal.

Context from parent:
<context>{context_for_agent}</context>

Your goal:
<goal>{goal}</goal>

Return ONLY a valid JSON object — no markdown, no explanation:
{{
    "action":             "fill | type | click | check | uncheck | upload_file | select | hover | scroll | wait_for_page | wait_for_element | inspect_page | hover | navigate | go_to_previous_page | wait_for_user | goal_completed",
    "selector":           "CSS selector (empty string if not needed)",
    "value":              "text / option value / URL (empty string if not needed)",
    "summary":            "what was just done (for logging)",
    "evidence":           "only when action=goal_completed — describe what on the page proves the goal is done"
}}

--- TERMINAL ACTIONS ---

goal_completed – Use ONLY when the goal is fully done AND you have verified it on the page.
                 Before returning this, confirm the evidence is visible in <page_elements>.
                 "evidence" field must describe the proof (e.g. 'field X now shows value Y').

wait_for_user  – Use when a human must take an action in the browser (CAPTCHA, 2FA, video).

--- ACTION REFERENCE ---

fill          – Clear an <input>/<textarea> and type new text. Does NOT press Enter.
type          – Type character-by-character without clearing (for autocomplete fields).
click         – Click buttons, links, custom dropdowns. NEVER use on checkboxes/radios.
check         – Set checkbox/radio to CHECKED (safe even if already checked).
uncheck       – Set checkbox to UNCHECKED (safe even if already unchecked).
upload_file   – selector=file input, value=absolute file path.
select        – Set a NATIVE <select>. value= must be the <option> value attribute.
inspect_page  – Dump ALL inputs/selects/textareas + buttons. Use when unsure of selectors.
wait_for_element – Wait until selector is visible. Use after click/select that adds new fields.
hover         – Hover to reveal hidden menus.
scroll        – value= "up" | "down" | pixel offset e.g. "500".
wait_for_page – Wait for page to finish loading.
navigate      – Go to URL in value=.
go_to_previous_page – Press browser Back.

--- RULES ---
- Dismiss cookie/consent banners FIRST.
- Always scroll into view before interacting.
- After navigation or major DOM change, use wait_for_page.
- If wait_for_element times out → immediately use inspect_page, never retry same selector.
- If the same action+selector repeats 3× with no progress → use inspect_page.
- For checkboxes/radios: always use check/uncheck, never click.
- For <select>: use action="select" with the option VALUE attribute.
- After submit click, check for validation errors before declaring goal_completed.
- Only return goal_completed when you can point to concrete evidence on the page.
"""


PARENT_SYSTEM_PROMPT = """
You are a supervisor agent. You do NOT interact with the browser.
You receive the result of a sub-agent that just attempted one goal, then decide what to do next.

Return ONLY a valid JSON object — no markdown, no explanation:
{
    "action":       "next | insert_steps | retry | return_to_user | done",
    "steps":        ["step1", "step2"],
    "retry_hint":   "extra context or corrected instructions for the sub-agent on retry",
    "message":      "explanation for the user (only for return_to_user or done)"
}

--- FIELD RULES ---
- "steps"       only when action=insert_steps. These are inserted BEFORE the next goal.
- "retry_hint"  only when action=retry. Be specific about what went wrong and how to fix it.
- "message"     only when action=return_to_user or done.

--- DECISION RULES ---

"next"
  → Sub-agent completed the goal AND the next goal is clear and atomic.
  → Use this by default when everything looks good.

"insert_steps"
  → Sub-agent completed the goal BUT the next goal is too broad, OR
    the result revealed page structure the next sub-agent needs to handle step-by-step.
  → Provide "steps": a list of specific atomic steps to insert BEFORE the next remaining goal.
  → Examples of when to insert:
      - Next goal is "fill the form" but sub-agent just navigated to a multi-section form.
      - Next goal is "submit" but sub-agent revealed there are required fields not yet filled.
      - Page has a multi-step wizard and remaining goals don't reflect the steps.

"retry"
  → Sub-agent failed (status=failed) but the goal is still achievable.
  → Provide "retry_hint" with what went wrong and what to try differently.
  → Do NOT retry more than 2 times for the same goal — use return_to_user instead.

"return_to_user"
  → Sub-agent returned wait_for_user, OR
    the goal genuinely requires a human decision or credential, OR
    retries are exhausted.
  → Provide "message" explaining what the user needs to do.

"done"
  → All goals are complete, OR remaining goals are now irrelevant given what happened.
  → Provide "message" summarizing what was accomplished.

--- WHEN TO INSERT STEPS (detailed) ---
Ask yourself: "Is the next goal specific enough for a sub-agent to execute in one focused session?"
If NO → insert_steps with a breakdown.
If YES → next.

Signs the next goal needs breaking down:
  1. It contains "and" (e.g. "fill form and submit").
  2. It's vague (e.g. "complete the checkout").
  3. The sub-agent's summary revealed unexpected page complexity.
  4. The next goal assumes UI state that hasn't been established yet.
"""


# ---------------------------------------------------------------------------
# Action executor (shared by sub-agent loop)
# ---------------------------------------------------------------------------

async def _execute_action(page, action: str, selector: str, value: str) -> str:
    """Execute a browser action and return a result string."""

    if action == "fill":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        await page.locator(selector).click()
        await page.locator(selector).fill(value)
        await wait_for_page_settled(page)
        await page.locator(selector).blur()
        return f"Filled '{selector}' with '{value}'"

    elif action == "type":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        await page.locator(selector).click()
        await page.locator(selector).type(value, delay=50)
        return f"Typed '{value}' into '{selector}'"

    elif action == "check":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        was = await page.locator(selector).is_checked()
        if not was:
            await page.locator(selector).click()
        await wait_for_dom_mutation(page, timeout=3_000)
        return f"Checked '{selector}' (was_checked={was})"

    elif action == "uncheck":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        was = await page.locator(selector).is_checked()
        if was:
            await page.locator(selector).click()
        await wait_for_dom_mutation(page, timeout=3_000)
        return f"Unchecked '{selector}' (was_checked={was})"

    elif action == "upload_file":
        await page.wait_for_selector(selector, state="attached", timeout=15_000)
        await page.locator(selector).set_input_files(value)
        await wait_for_dom_mutation(page, timeout=5_000)
        return f"Uploaded '{value}' to '{selector}'"

    elif action == "click":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        el_type = await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el ? (el.type || el.tagName.toLowerCase()) : null; }",
            selector,
        )
        el_text = await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el ? (el.innerText || el.value || '').trim().toLowerCase() : ''; }",
            selector,
        )
        is_submit = el_type == "submit" or any(
            w in el_text for w in ("next", "submit", "continue", "proceed", "apply", "save")
        )
        await page.locator(selector).click()
        await wait_for_page_settled(page)
        if is_submit:
            errors = await get_form_errors(page)
            if errors:
                return (
                    f"Clicked '{selector}' (submit). "
                    f"⚠️ VALIDATION ERRORS — fix ALL before submitting again:\n"
                    f"{json.dumps(errors, indent=2)}\n"
                    "Fix each field, then click submit again."
                )
            return f"Clicked '{selector}' (submit) — no validation errors"
        return f"Clicked '{selector}'"

    elif action == "select":
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
        await scroll_into_view(page, selector)
        value_before = await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el?.value ?? null; }", selector
        )
        try:
            selected = await page.locator(selector).select_option(value=value)
            if not selected:
                raise ValueError("No option matched by value")
            result = f"Selected value='{value}' in '{selector}'"
        except Exception:
            await page.locator(selector).select_option(label=value)
            result = f"Selected label='{value}' in '{selector}'"
        value_after = await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el?.value ?? null; }", selector
        )
        if value_before == value_after:
            return result + f" | ⚠️ dropdown value did NOT change (still '{value_after}'). Option may be disabled or JS-controlled."
        mutated = await wait_for_dom_mutation(page, timeout=8_000)
        return result + (
            " | ✅ Page re-rendered — check <page_elements> before next action."
            if mutated else " | No DOM change after selection"
        )

    elif action == "wait_for_element":
        try:
            await page.wait_for_selector(selector, state="visible", timeout=15_000)
            await wait_for_page_settled(page, timeout=10_000)
            return f"✅ Element '{selector}' is now visible"
        except PlaywrightTimeoutError:
            all_inputs = await get_all_inputs(page)
            return (
                f"❌ Timeout: '{selector}' did not appear within 15s.\n"
                f"ALL inputs in DOM:\n{json.dumps(all_inputs, indent=2)}\n"
                "Do NOT retry same selector. Pick correct one from list above."
            )

    elif action == "inspect_page":
        all_inputs = await get_all_inputs(page)
        all_buttons = await page.evaluate(r"""() => {
            function bestSelector(el) {
                if (el.id) return /[.:#\[\]()~+>]/.test(el.id) ? `[id="${el.id}"]` : '#' + el.id;
                if (el.name) return `[name="${el.name}"]`;
                const ai = el.getAttribute('data-ai-id');
                if (ai) return `[data-ai-id="${ai}"]`;
                return el.tagName.toLowerCase();
            }
            function isVisible(el) {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 &&
                       window.getComputedStyle(el).display !== 'none' &&
                       window.getComputedStyle(el).visibility !== 'hidden';
            }
            return [...document.querySelectorAll(
                'button, input[type=submit], input[type=button], [role=button], a[href]'
            )].map(el => ({
                selector: bestSelector(el),
                tag:      el.tagName.toLowerCase(),
                type:     el.type || el.getAttribute('role') || null,
                text:     (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                visible:  isVisible(el),
                disabled: el.disabled || false,
            }));
        }""")
        return (
            f"inspect_page:\nFORM FIELDS:\n{json.dumps(all_inputs, indent=2)}\n\n"
            f"BUTTONS/LINKS:\n{json.dumps(all_buttons, indent=2)}\n"
            "Use 'selector' from this list. Verify 'visible' and 'disabled' before use."
        )

    elif action == "hover":
        await page.wait_for_selector(selector, state="visible", timeout=10_000)
        await scroll_into_view(page, selector)
        await page.locator(selector).hover()
        await asyncio.sleep(0.4)
        return f"Hovered over '{selector}'"

    elif action == "scroll":
        if value == "up":
            await page.keyboard.press("Home")
        elif value == "down":
            await page.keyboard.press("End")
        else:
            try:
                await page.evaluate(f"window.scrollBy(0, {int(value)})")
            except ValueError:
                await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.3)
        return f"Scrolled '{value}'"

    elif action == "wait_for_page":
        await wait_for_page_settled(page, timeout=30_000)
        return "Page fully loaded"

    elif action == "navigate":
        await page.goto(value)
        await wait_for_page_settled(page)
        return f"Navigated to '{value}'"

    elif action == "go_to_previous_page":
        await page.go_back()
        await wait_for_page_settled(page)
        return "Went back to previous page"

    return f"Unrecognized action: '{action}'"


# ---------------------------------------------------------------------------
# Sub-agent runner
# ---------------------------------------------------------------------------

async def _run_sub_agent(
    page,
    goal: str,
    context_for_agent: str,
    completed_so_far: list[dict],
    retry_hint: str = "",
    max_steps: int = 30,
) -> dict:
    """
    Run a focused sub-agent for a single goal.
    Returns a result dict:
      status:   "completed" | "failed" | "wait_for_user"
      summary:  human-readable description of what happened
      evidence: proof the goal is done (only on completed)
      url:      current page URL when sub-agent finished
      message:  details for parent/user (on   wait_for_user / failed)
    """
    system_prompt = _sub_agent_system_prompt(goal, context_for_agent)
    if retry_hint:
        system_prompt += f"\n\n--- RETRY HINT FROM PARENT ---\n{retry_hint}\n"

    agent = await goal_based_agent(system_prompt)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    last_result = "Starting. Use inspect_page to assess the current page state."
    error_streak = 0
    LOOP_THRESHOLD = 3
    recent_actions: list[tuple] = []

    print(f"\n[SUB-AGENT] Starting goal: {goal!r}")

    for step in range(max_steps):
        snapshot = await get_labeled_snapshot(page)

        message = f"""
<last_action_result>{last_result}</last_action_result>
<current_url>{page.url}</current_url>
<completed_goals_so_far>{json.dumps(completed_so_far, indent=2)}</completed_goals_so_far>
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
                raise ValueError("Empty response from sub-agent")

            parsed = json.loads(cleaned)
            if "action" not in parsed:
                raise ValueError(f"Missing 'action' key: {cleaned[:200]}")

            action   = parsed["action"]
            selector = parsed.get("selector", "")
            value    = parsed.get("value", "")
            summary  = parsed.get("summary", "")
            evidence = parsed.get("evidence", "")

            print(f"[SUB-AGENT] step={step} action={action} selector={selector!r} value={str(value)[:60]!r}")

            # ── Terminal actions ───────────────────────────────────────────
            if action == "goal_completed":
                print(f"[SUB-AGENT] ✅ Goal completed. Evidence: {evidence}")
                return {
                    "status":   "completed",
                    "summary":  summary or evidence,
                    "evidence": evidence,
                    "url":      page.url,
                }

            # if action == "ask_parent":
            #     print(f"[SUB-AGENT] ❓ Asking parent: {value}")
            #     return {
            #         "status":  "ask_parent",
            #         "summary": summary,
            #         "message": value,
            #         "url":     page.url,
            #     }

            if action == "wait_for_user":
                print(f"[SUB-AGENT] 👤 Needs user interaction")
                return {
                    "status":  "wait_for_user",
                    "summary": summary,
                    "message": value or "Human interaction required in the browser.",
                    "url":     page.url,
                }

            # ── Loop guard ─────────────────────────────────────────────────
            hashable_value = json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
            key = (action, selector, hashable_value)
            recent_actions.append(key)
            if len(recent_actions) > LOOP_THRESHOLD:
                recent_actions.pop(0)

            if len(recent_actions) == LOOP_THRESHOLD and len(set(recent_actions)) == 1:
                all_inputs = await get_all_inputs(page)
                current_value = None
                if selector:
                    try:
                        current_value = await page.evaluate(
                            "(sel) => { const el = document.querySelector(sel); return el ? el.value : null; }",
                            selector,
                        )
                    except Exception:
                        pass
                last_result = (
                    f"⚠️ LOOP DETECTED — action '{action}' on '{selector}' repeated "
                    f"{LOOP_THRESHOLD}× with no progress.\n"
                    f"Current value of '{selector}': {current_value!r}\n"
                    f"ALL inputs in DOM:\n{json.dumps(all_inputs, indent=2)}\n"
                    "STOP retrying. Use inspect_page and pick the correct selector."
                )
                recent_actions.clear()
                print(f"[LOOP-GUARD] Injected diagnostic for selector={selector!r}")
                error_streak = 0
                continue

            # ── Execute action ─────────────────────────────────────────────
            last_result = await _execute_action(page, action, selector, value)
            error_streak = 0

        except Exception as e:
            error_streak += 1
            last_result = f"Error: {e}"
            print(f"[SUB-AGENT ERROR] streak={error_streak} {e}")
            if error_streak >= 3:
                return {
                    "status":  "failed",
                    "summary": f"Aborted after 3 consecutive errors.",
                    "message": f"Last error: {e}",
                    "url":     page.url,
                }

    return {
        "status":  "failed",
        "summary": f"Reached {max_steps}-step limit without completing goal.",
        "message": f"Goal may be too complex or the page behaved unexpectedly.",
        "url":     page.url,
    }


# ---------------------------------------------------------------------------
# Parent supervisor
# ---------------------------------------------------------------------------

async def _run_parent_decision(
    parent_agent,
    config: dict,
    current_goal: str,
    sub_result: dict,
    remaining_goals: list[str],
    completed_goals: list[dict],
    retry_count: int,
) -> dict:
    """Ask the parent agent to evaluate the sub-agent result and decide next action."""
    message = f"""
<current_goal>{current_goal}</current_goal>
<sub_agent_result>{json.dumps(sub_result, indent=2)}</sub_agent_result>
<retry_count>{retry_count}</retry_count>
<completed_goals>{json.dumps(completed_goals, indent=2)}</completed_goals>
<remaining_goals>{json.dumps(remaining_goals, indent=2)}</remaining_goals>
"""
    response = await parent_agent.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )
    raw = response["messages"][-1].content or ""
    cleaned = _clean_json(raw)
    parsed = json.loads(cleaned)
    print(f"[PARENT] decision={parsed.get('action')} retry_count={retry_count}")
    return parsed


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@tool
async def control_web_browser_tool(
    default_browser_path: str,
    url: str,
    context_for_agent: str,
    goals: list[str],
) -> str:
    """Use this tool whenever the user wants to DO something on a website."""
    print(f"[BROWSER] url={url} goals={goals}")

    browser = None
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

        goals = list(goals)  # mutable — parent may insert sub-steps at runtime

        parent_agent = await goal_based_agent(PARENT_SYSTEM_PROMPT)
        parent_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        completed_goals: list[dict] = []
        i = 0
        MAX_RETRIES = 2

        while i < len(goals):
            current_goal = goals[i]
            retry_count = 0
            retry_hint = ""

            print(f"\n[PARENT] Launching sub-agent for goal {i}/{len(goals)-1}: {current_goal!r}")

            while True:  # retry loop for current goal
                sub_result = await _run_sub_agent(
                    page=page,
                    goal=current_goal,
                    context_for_agent=context_for_agent,
                    completed_so_far=completed_goals,
                    retry_hint=retry_hint,
                )

                # Parent evaluates the result
                try:
                    decision = await _run_parent_decision(
                        parent_agent=parent_agent,
                        config=parent_config,
                        current_goal=current_goal,
                        sub_result=sub_result,
                        remaining_goals=goals[i + 1:],
                        completed_goals=completed_goals,
                        retry_count=retry_count,
                    )
                except Exception as e:
                    print(f"[PARENT ERROR] Failed to parse decision: {e}")
                    decision = {"action": "retry", "retry_hint": f"Parent decision failed: {e}"}

                action = decision.get("action", "retry")

                if action == "next":
                    completed_goals.append({
                        "index":   i,
                        "goal":    current_goal,
                        "summary": sub_result.get("summary", ""),
                    })
                    print(f"[PARENT] ✅ Goal {i} accepted. Moving to next.")
                    i += 1
                    break

                elif action == "insert_steps":
                    new_steps = decision.get("steps", [])
                    if new_steps:
                        insert_at = i + 1
                        goals[insert_at:insert_at] = new_steps
                        print(f"[PARENT] 📋 Inserted {len(new_steps)} steps after goal {i}: {new_steps}")
                    completed_goals.append({
                        "index":   i,
                        "goal":    current_goal,
                        "summary": sub_result.get("summary", ""),
                    })
                    i += 1
                    break

                elif action == "retry":
                    retry_count += 1
                    retry_hint = decision.get("retry_hint", "")
                    print(f"[PARENT] 🔄 Retrying goal {i} (attempt {retry_count}/{MAX_RETRIES}). Hint: {retry_hint}")
                    if retry_count > MAX_RETRIES:
                        await browser.close()
                        return (
                            f"Goal failed after {MAX_RETRIES} retries: {current_goal!r}\n"
                            f"Last result: {sub_result.get('message', sub_result.get('summary', ''))}\n"
                            f"Completed so far: {json.dumps(completed_goals, indent=2)}"
                        )

                elif action == "return_to_user":
                    await browser.close()
                    return (
                        f"Paused — human input required.\n"
                        f"Message: {decision.get('message', sub_result.get('message', ''))}\n"
                        f"Current URL: {sub_result.get('url', '')}\n"
                        f"Completed so far: {json.dumps(completed_goals, indent=2)}"
                    )

                elif action == "done":
                    await browser.close()
                    completed_goals.append({
                        "index":   i,
                        "goal":    current_goal,
                        "summary": sub_result.get("summary", ""),
                    })
                    return (
                        f"All done.\n"
                        f"{decision.get('message', '')}\n"
                        f"Completed: {json.dumps(completed_goals, indent=2)}"
                    )

                else:
                    print(f"[PARENT] Unknown action '{action}', defaulting to retry.")
                    retry_count += 1
                    if retry_count > MAX_RETRIES:
                        await browser.close()
                        return f"Aborted — unknown parent action '{action}' after {MAX_RETRIES} retries."

        # All goals processed
        await browser.close()
        return f"All goals completed:\n{json.dumps(completed_goals, indent=2)}"

    except Exception as e:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        return (
            f"Fatal exception: {e}\n"
            "DO NOT reopen the browser. Please explain what happened to the user."
        )
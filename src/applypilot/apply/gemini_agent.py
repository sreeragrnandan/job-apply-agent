"""Native Playwright + Gemini agent for autonomous job applications.

Replaces the Claude Code CLI subprocess with a pure-Python agentic loop:
  1. Connects to Chrome via CDP (launched by chrome.py).
  2. Takes an accessibility snapshot of the page.
  3. Sends the snapshot + prompt to Gemini via function calling.
  4. Executes the returned browser actions (click, fill, select, upload, etc.).
  5. Repeats until Gemini calls finish() or the turn limit is hit.

Uses the same GEMINI_API_KEY + model from the environment as the rest of
ApplyPilot -- no extra SDKs required (calls the REST API via httpx, same as
llm.py).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright, Page, Browser

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_MODEL = "gemini-3.8-flash"
_MAX_TURNS = int(os.environ.get("MAX_TURNS", "150"))  # safety cap per job (Workday can have many pages)
_TIMEOUT_S = 120          # seconds for LLM call
_ACTION_DELAY = 0.25      # pause between browser actions (seconds)
_SCREENSHOT_QUALITY = 60  # JPEG quality for screenshots sent to Gemini

# ---------------------------------------------------------------------------
# Tool definitions (function calling spec sent to Gemini)
# ---------------------------------------------------------------------------

BROWSER_TOOLS: list[dict] = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click an element identified by CSS selector or visible text. "
            "Prefer CSS selectors. Use text= prefix for text matching, e.g. text=Submit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector or text=<label>"}
            },
            "required": ["selector"],
        },
    },
    {
        "name": "fill",
        "description": "Clear and fill a text input or textarea with a value.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input"},
                "value":    {"type": "string", "description": "Text to type"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "select_option",
        "description": "Select a <select> dropdown option by label or value.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <select>"},
                "label":    {"type": "string", "description": "Option label or value to select"},
            },
            "required": ["selector", "label"],
        },
    },
    {
        "name": "upload_file",
        "description": "Upload a file to a file input element.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector":  {"type": "string", "description": "CSS selector for <input type=file>"},
                "file_path": {"type": "string", "description": "Absolute path to the file"},
            },
            "required": ["selector", "file_path"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page in a direction.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["down", "up", "bottom", "top"],
                    "description": "Scroll direction",
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for a number of milliseconds (max 5000).",
        "parameters": {
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "description": "Milliseconds to wait (max 5000)"}
            },
            "required": ["ms"],
        },
    },
    {
        "name": "get_page_snapshot",
        "description": (
            "Take a fresh screenshot and accessibility snapshot of the current page. "
            "Call this after navigation or when you need to re-read the page state."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "type_text",
        "description": (
            "Type text into the currently focused element using keyboard events. "
            "Use this for Workday/React inputs that don't respond to fill(). "
            "First click the element, then call type_text to type into it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "clear_first": {
                    "type": "boolean",
                    "description": "If true, select all and delete before typing (default true)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown, ArrowUp).",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name, e.g. Enter, Tab, Escape, ArrowDown"}
            },
            "required": ["key"],
        },
    },
    {
        "name": "execute_actions",
        "description": (
            "Execute a batch of browser actions sequentially in a single turn without waiting for an LLM round-trip between each. "
            "Use this to fill multiple form fields, select options, or click buttons on the current page all at once. "
            "Actions supported: click, fill, type_text, press_key, select_option, wait, scroll."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "description": "List of actions to execute in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["click", "fill", "type_text", "press_key", "select_option", "wait", "scroll"],
                                "description": "Action type",
                            },
                            "selector": {"type": "string", "description": "CSS selector or text=<label> (for click, fill, select_option)"},
                            "value": {"type": "string", "description": "Value to fill or text to type"},
                            "text": {"type": "string", "description": "Text to type (for type_text)"},
                            "key": {"type": "string", "description": "Key name (for press_key, e.g. Tab, Enter, ArrowDown)"},
                            "direction": {"type": "string", "enum": ["down", "up", "bottom", "top"], "description": "Direction to scroll"},
                            "ms": {"type": "integer", "description": "Milliseconds to wait"},
                        },
                        "required": ["action"],
                    },
                }
            },
            "required": ["actions"],
        },
    },
    {
        "name": "record_learning",
        "description": (
            "Save a newly discovered question answer or ATS form pattern to learnings.json so it is remembered for future jobs. "
            "category can be 'screening_answers', 'ats_patterns.<ats_name>' (e.g. 'ats_patterns.workday'), or 'site_specific.<employer>'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to store under, e.g. 'screening_answers', 'ats_patterns.workday', or 'site_specific.thomson_reuters'",
                },
                "key": {"type": "string", "description": "Question name or pattern name (e.g. 'restrictive_covenants', 'phone_number_format')"},
                "value": {"type": "string", "description": "The answer or pattern string"},
            },
            "required": ["category", "key", "value"],
        },
    },
    {
        "name": "fill_date",
        "description": (
            "Fill a month/year date field in Workday (e.g. From*, To*). "
            "label is the field label (e.g. 'From*', 'To*'). "
            "month is 2-digit month (e.g. '06' or '07'). year is 4-digit year (e.g. '2021' or '2025')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Field label (e.g. 'From*', 'To*')"},
                "month": {"type": "string", "description": "Month MM (e.g. '06', '07')"},
                "year": {"type": "string", "description": "Year YYYY (e.g. '2021', '2025')"},
            },
            "required": ["label", "month", "year"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Signal the end of the application session. "
            "result must be APPLIED, FAILED, EXPIRED, CAPTCHA, or LOGIN_ISSUE. "
            "For FAILED include a short snake_case reason. "
            "learnings: optional list of newly learned answers or ATS form patterns discovered during this run to persist into learnings.json."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "enum": ["APPLIED", "FAILED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"],
                },
                "reason": {"type": "string", "description": "Short reason (FAILED only)"},
                "learnings": {
                    "type": "array",
                    "description": "Optional list of newly learned question answers or ATS patterns to remember for future jobs",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "description": "e.g. 'screening_answers', 'ats_patterns.workday', or 'site_specific.<employer>'",
                            },
                            "key": {"type": "string", "description": "Question name or pattern name"},
                            "value": {"type": "string", "description": "Answer or pattern value"},
                        },
                        "required": ["category", "key", "value"],
                    },
                },
            },
            "required": ["result"],
        },
    },
]


# ---------------------------------------------------------------------------
# Gemini API helper
# ---------------------------------------------------------------------------

def _call_gemini(
    api_key: str,
    model: str,
    system_prompt: str,
    history: list[dict],
    screenshot_b64: str | None,
    ax_snapshot: str,
) -> dict:
    """Send a turn to the Gemini generateContent API and return the response dict."""
    user_parts: list[dict] = []
    if screenshot_b64:
        user_parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": screenshot_b64,
            }
        })
    user_parts.append({"text": f"[Page accessibility snapshot]\n{ax_snapshot[:8000]}"})

    contents = list(history) + [{"role": "user", "parts": user_parts}]

    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"functionDeclarations": BROWSER_TOOLS}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
        },
    }

    url = f"{_GEMINI_NATIVE_BASE}/models/{model}:generateContent"
    _MAX_API_RETRIES = 4
    for attempt in range(_MAX_API_RETRIES):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                resp = client.post(url, json=payload, params={"key": api_key})
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, Exception) as exc:
            # Catch SSL errors, connection resets, and other transient network issues
            if attempt < _MAX_API_RETRIES - 1:
                wait = min(10 * (2 ** attempt), 60)
                log.warning("Gemini connection error (attempt %d/%d): %s — retrying in %.0fs...",
                            attempt + 1, _MAX_API_RETRIES, exc, wait)
                time.sleep(wait)
                continue
            raise
        if resp.status_code in (429, 503) and attempt < _MAX_API_RETRIES - 1:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else min(10 * (2 ** attempt), 60)
            except (ValueError, TypeError):
                wait = min(10 * (2 ** attempt), 60)
            log.warning("Gemini %s (attempt %d/%d), retrying in %.0fs...",
                        resp.status_code, attempt + 1, _MAX_API_RETRIES, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Page observation helpers
# ---------------------------------------------------------------------------

def _take_screenshot(page: Page) -> str:
    """Return base64-encoded JPEG screenshot of the current viewport."""
    try:
        raw = page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY, full_page=False)
        return base64.b64encode(raw).decode()
    except Exception as exc:
        log.warning("Screenshot failed: %s", exc)
        return ""


def _get_ax_snapshot(page: Page) -> str:
    """Return an accessibility-tree snapshot as a compact JSON string."""
    try:
        snap = page.accessibility.snapshot(interesting_only=True)
        if snap:
            return json.dumps(snap, ensure_ascii=False)[:8000]
    except Exception:
        pass
    try:
        return page.inner_text("body")[:6000]
    except Exception:
        return "(unable to read page)"


def _observe_page(page: Page) -> tuple[str, str]:
    """Return (screenshot_b64, ax_snapshot) for the current page state."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    return _take_screenshot(page), _get_ax_snapshot(page)


# ---------------------------------------------------------------------------
# Browser action executor
# ---------------------------------------------------------------------------

def _execute_action(page: Page, name: str, args: dict, log_fn) -> str:
    """Execute one tool call and return a short status string."""
    try:
        if name == "navigate":
            url = args["url"]
            curr = getattr(page, "url", "")
            if "/apply" in curr and "/apply" not in url:
                log_fn(f"navigate blocked: already inside application ({curr[:40]})")
                return (
                    "BLOCKED: You are already inside the active application form! "
                    "Do NOT navigate back to the job description or restart the application. "
                    "Stay on the current page, check which fields need to be filled, and fill them."
                )
            log_fn(f"navigate {url[:60]}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"navigated to {url[:60]}"

        elif name == "click":
            sel = args["selector"]
            # Auto-resolve common Workday source selectors
            if 'data-automation-id="source"' in sel or 'data-automation-id="source"]' in sel or sel == "text=0 items selected":
                sel = '#source--source, [data-automation-id="formField-source"] input, ' + sel
            # Block navigating backwards out of the application form
            if "Back to Job Posting" in sel or "backToJobPosting" in sel:
                log_fn("click blocked: Back to Job Posting")
                return (
                    "BLOCKED: Do NOT click 'Back to Job Posting'! "
                    "Stay on the application form, complete sign in or fill required fields."
                )
            # Auto-resolve clicking date labels to their input
            if sel in ("text=From*", "text=To*", "text=From", "text=To"):
                clean = sel[5:]
                date_inputs = page.locator(f"div:has(label:has-text('{clean}')) input, fieldset:has-text('{clean}') input").all()
                if date_inputs:
                    date_inputs[0].click(timeout=3000)
                    time.sleep(_ACTION_DELAY * 0.5)
                    return f"clicked date input for {clean}"
            # Redirect Sign In button to form submit button if present on page
            if sel in ("text=Sign In", "text=Sign in", "text=Log In", "text=Log in"):
                submit_btn = page.locator("[data-automation-id='signInSubmitButton']").first
                if submit_btn.is_visible():
                    sel = "[data-automation-id='signInSubmitButton']"
            # Workday Canvas buttons with click_filter overlay: force click directly
            if "signInSubmitButton" in sel or "createAccountSubmitButton" in sel:
                log_fn(f"click {sel[:50]} (force)")
                try:
                    el = page.locator(sel).first
                    el.wait_for(state="visible", timeout=5000)
                    el.click(force=True, timeout=3000)
                    time.sleep(3.5)  # Allow Workday auth & navigation to complete
                    return f"clicked {sel[:50]}"
                except Exception:
                    pass
            if any(k in sel for k in ("SignInWithEmailButton", "click_filter")):
                log_fn(f"click {sel[:50]} (force)")
                try:
                    el = page.locator(sel).first
                    el.wait_for(state="visible", timeout=5000)
                    el.click(force=True, timeout=3000)
                    time.sleep(1.5)
                    return f"clicked {sel[:50]}"
                except Exception:
                    pass
            log_fn(f"click {sel[:50]}")
            try:
                if sel.startswith("text="):
                    el = page.get_by_text(sel[5:], exact=False).first
                else:
                    el = page.locator(sel).first
                # Wait up to 5s for element to appear (Workday renders lazily)
                el.wait_for(state="visible", timeout=5000)
                el.click(timeout=4000)
            except Exception:
                # Fallback: scroll into view and force-click; if off-screen/timeout, scroll up first
                try:
                    if sel.startswith("text="):
                        el = page.get_by_text(sel[5:], exact=False).first
                    else:
                        el = page.locator(sel).first
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        page.evaluate("window.scrollTo(0, 0)")
                        time.sleep(0.3)
                    el.click(force=True, timeout=3000)
                except Exception as exc2:
                    return f"error: click failed: {exc2}"
            time.sleep(_ACTION_DELAY)
            if "Job Board" in sel:
                time.sleep(1.0)  # Workday needs ~1s to fetch sub-sources like LinkedIn
            return f"clicked {sel[:50]}"

        elif name == "fill_date":
            label = args.get("label", args.get("selector", ""))
            val = str(args.get("value", args.get("text", "")))
            month = str(args.get("month", ""))
            year = str(args.get("year", ""))
            if not month or not year:
                parts = val.replace("-", "/").split("/")
                if len(parts) >= 2:
                    month, year = parts[0].zfill(2), parts[1]
                elif len(parts) == 1 and len(parts[0]) == 4:
                    month, year = "01", parts[0]
            clean_label = label.replace("text=", "").strip()
            log_fn(f"fill_date {clean_label} = {month}/{year}")
            try:
                container = page.locator(f"div:has(label:has-text('{clean_label}')), fieldset:has-text('{clean_label}')").first
                inputs = container.locator("input").all()
                if len(inputs) >= 2:
                    inputs[0].click(timeout=3000)
                    time.sleep(0.1)
                    inputs[0].fill(month.zfill(2), timeout=2000)
                    time.sleep(0.1)
                    inputs[1].click(timeout=3000)
                    time.sleep(0.1)
                    inputs[1].fill(year, timeout=2000)
                    time.sleep(_ACTION_DELAY * 0.5)
                    return f"filled date {clean_label}: {month}/{year}"
                elif len(inputs) == 1:
                    inputs[0].click(timeout=3000)
                    inputs[0].fill(f"{month.zfill(2)}/{year}", timeout=2000)
                    time.sleep(_ACTION_DELAY * 0.5)
                    return f"filled date {clean_label}: {month}/{year}"
            except Exception:
                pass
            try:
                mm_input = page.locator(f"div:has-text('{clean_label}') input[placeholder='MM'], div:has-text('{clean_label}') input[aria-label='MM']").first
                yyyy_input = page.locator(f"div:has-text('{clean_label}') input[placeholder='YYYY'], div:has-text('{clean_label}') input[aria-label='YYYY']").first
                mm_input.fill(month.zfill(2), timeout=2000)
                yyyy_input.fill(year, timeout=2000)
                time.sleep(_ACTION_DELAY * 0.5)
                return f"filled date {clean_label}: {month}/{year} (fallback)"
            except Exception as exc2:
                return f"error: fill_date failed: {exc2}"

        elif name == "fill":
            sel, val = args["selector"], args.get("value", args.get("text", ""))
            log_fn(f"fill {sel[:40]} = {val[:30]}")
            # If selector starts with text=, it's typically a label; try get_by_label first
            if sel.startswith("text="):
                label_text = sel[5:]
                try:
                    el = page.get_by_label(label_text, exact=False).first
                    el.fill(val, timeout=2500)
                    time.sleep(_ACTION_DELAY * 0.5)
                    return f"filled label {label_text[:30]}"
                except Exception:
                    pass
                # Otherwise click the label text and type with keyboard
                try:
                    el = page.get_by_text(label_text, exact=False).first
                    el.click(timeout=3000)
                    time.sleep(0.1)
                    page.keyboard.press("Control+a")
                    page.keyboard.press("Delete")
                    page.keyboard.type(val, delay=10)
                    time.sleep(_ACTION_DELAY * 0.5)
                    return f"typed into label {label_text[:30]}"
                except Exception as exc2:
                    return f"error: fill label failed: {exc2}"

            # Standard locator fill
            try:
                el = page.locator(sel).first
                el.fill(val, timeout=3000)
                time.sleep(_ACTION_DELAY * 0.5)
                return f"filled {sel[:40]}"
            except Exception:
                pass
            # Fallback: click + select-all + keyboard type (works for React/Workday)
            try:
                el = page.locator(sel).first
                el.click(timeout=3000)
                time.sleep(0.1)
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                page.keyboard.type(val, delay=10)
                time.sleep(_ACTION_DELAY * 0.5)
                return f"typed into {sel[:40]} (keyboard fallback)"
            except Exception as exc2:
                return f"error: fill and keyboard fallback both failed: {exc2}"

        elif name == "type_text":
            text = args.get("text", args.get("value", ""))
            clear_first = args.get("clear_first", True)
            log_fn(f"type_text {text[:40]}")
            if clear_first:
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                time.sleep(0.05)
            page.keyboard.type(text, delay=10)
            time.sleep(_ACTION_DELAY * 0.5)
            return f"typed: {text[:40]}"

        elif name == "press_key":
            key = args["key"]
            log_fn(f"press_key {key}")
            page.keyboard.press(key)
            time.sleep(_ACTION_DELAY * 0.3)
            return f"pressed {key}"

        elif name == "select_option":
            sel, label = args["selector"], args["label"]
            log_fn(f"select {sel[:40]} -> {label[:30]}")
            try:
                page.locator(sel).first.select_option(label=label, timeout=10000)
            except Exception:
                page.locator(sel).first.select_option(value=label, timeout=10000)
            time.sleep(_ACTION_DELAY * 0.5)
            return f"selected {label[:30]}"

        elif name == "upload_file":
            sel, fpath = args["selector"], args["file_path"]
            fname = Path(fpath).name
            log_fn(f"upload {fname}")

            # Strategy 1: Direct set_input_files on provided selector
            try:
                el = page.locator(sel).first
                el.set_input_files(fpath, timeout=8000)
                time.sleep(_ACTION_DELAY)
                return f"uploaded {fname} (direct)"
            except Exception:
                pass

            # Strategy 2: Find ANY hidden file input on the page and set directly
            try:
                file_inputs = page.locator("input[type='file']").all()
                for fi in file_inputs:
                    try:
                        fi.set_input_files(fpath, timeout=3000)
                        time.sleep(_ACTION_DELAY)
                        return f"uploaded {fname} (hidden input)"
                    except Exception:
                        continue
            except Exception:
                pass

            # Strategy 3: Use file chooser via clicking the upload button/zone
            try:
                upload_triggers = [
                    "text=Select files", "text=Upload", "text=Attach",
                    "text=Choose File", "text=Browse",
                    "[data-automation-id='file-upload-input-ref']",
                    "[class*='upload']", "[class*='drop']",
                ]
                for trigger in upload_triggers:
                    try:
                        if trigger.startswith("text="):
                            btn = page.get_by_text(trigger[5:], exact=False).first
                        else:
                            btn = page.locator(trigger).first
                        if btn.is_visible(timeout=2000):
                            with page.expect_file_chooser(timeout=5000) as fc_info:
                                btn.click(timeout=3000)
                            fc_info.value.set_files(fpath)
                            time.sleep(_ACTION_DELAY)
                            return f"uploaded {fname} (file chooser via {trigger})"
                    except Exception:
                        continue
            except Exception:
                pass

            # Strategy 4: JS override — make hidden input visible then set files
            try:
                page.evaluate("""
                    () => {
                        const inputs = document.querySelectorAll('input[type="file"]');
                        inputs.forEach(el => {
                            el.style.display = 'block';
                            el.style.opacity = '1';
                            el.style.visibility = 'visible';
                            el.style.position = 'static';
                            el.removeAttribute('tabindex');
                        });
                    }
                """)
                time.sleep(0.5)
                fi_all = page.locator("input[type='file']").all()
                for fi in fi_all:
                    try:
                        fi.set_input_files(fpath, timeout=3000)
                        time.sleep(_ACTION_DELAY)
                        return f"uploaded {fname} (JS reveal)"
                    except Exception:
                        continue
            except Exception as exc_final:
                pass

            return f"error: all upload strategies failed for {fname}"

        elif name == "scroll":
            direction = args["direction"]
            log_fn(f"scroll {direction}")
            key = {"down": "PageDown", "up": "PageUp", "bottom": "End", "top": "Home"}[direction]
            page.keyboard.press(key)
            time.sleep(_ACTION_DELAY * 0.5)
            return f"scrolled {direction}"

        elif name == "wait":
            ms = min(int(args.get("ms", 1000)), 5000)
            log_fn(f"wait {ms}ms")
            time.sleep(ms / 1000)
            return f"waited {ms}ms"

        elif name == "get_page_snapshot":
            log_fn("snapshot")
            return "snapshot_requested"

        elif name == "execute_actions":
            actions_list = args.get("actions", [])
            log_fn(f"execute_actions ({len(actions_list)} steps)")
            sub_results = []
            for idx, act in enumerate(actions_list):
                act_name = act.get("action", "")
                sub_args = dict(act)
                sub_args.pop("action", None)
                if act_name == "type_text" and "value" in sub_args and "text" not in sub_args:
                    sub_args["text"] = sub_args["value"]
                elif act_name == "fill" and "text" in sub_args and "value" not in sub_args:
                    sub_args["value"] = sub_args["text"]
                res = _execute_action(page, act_name, sub_args, log_fn)
                sub_results.append(f"[{idx+1}/{len(actions_list)}] {act_name}: {res}")
                if res.startswith("FINISH:"):
                    return res
            return "\n".join(sub_results)

        elif name == "record_learning":
            cat = args.get("category", "screening_answers")
            key = args.get("key", "")
            val = args.get("value", "")
            log_fn(f"record_learning: [{cat}] {key} = {str(val)[:40]}")
            try:
                from applypilot.config import save_learning
                save_learning(cat, key, val)
                return f"recorded learning: [{cat}] {key}"
            except Exception as rec_exc:
                return f"error recording learning: {rec_exc}"

        elif name == "finish":
            result = args["result"]
            reason = args.get("reason", "")
            learnings = args.get("learnings", [])
            if isinstance(learnings, list) and learnings:
                from applypilot.config import save_learning
                for item in learnings:
                    if isinstance(item, dict):
                        cat = item.get("category", "screening_answers")
                        k = item.get("key", "")
                        v = item.get("value", "")
                        if k and v:
                            save_learning(cat, k, v)
                            log_fn(f"auto-saved learning from finish(): [{cat}] {k} = {str(v)[:40]}")
            return f"FINISH:{result}:{reason}"

        else:
            log.warning("Unknown tool: %s", name)
            return f"unknown tool: {name}"

    except Exception as exc:
        log.warning("Action %s failed: %s", name, exc)
        return f"error: {exc}"




# ---------------------------------------------------------------------------
# History pruning helper
# ---------------------------------------------------------------------------

def _prune_history_images(history: list[dict], keep_recent: int = 3) -> list[dict]:
    """Strip base64 image data from history entries older than keep_recent turns.

    Each 'turn' in history is 2 entries (user observation + model response).
    We keep images for the most recent keep_recent turns to cap context size.
    """
    pruned = []
    # Count user turns (each has the screenshot)
    user_turn_indices = [i for i, h in enumerate(history) if h.get("role") == "user"
                         and any("inlineData" in p for p in h.get("parts", []))]
    cutoff = len(user_turn_indices) - keep_recent
    old_image_turns = set(user_turn_indices[:cutoff]) if cutoff > 0 else set()

    for idx, entry in enumerate(history):
        if idx in old_image_turns:
            # Strip inlineData from old user turns, keep text parts
            new_parts = [p for p in entry.get("parts", []) if "inlineData" not in p]
            if not new_parts:
                new_parts = [{"text": "[page snapshot omitted]"}]
            pruned.append({"role": entry["role"], "parts": new_parts})
        else:
            pruned.append(entry)
    return pruned


# ---------------------------------------------------------------------------
# Public API: run_agent
# ---------------------------------------------------------------------------

def run_agent(
    job: dict,
    cdp_port: int,
    system_prompt: str,
    worker_id: int = 0,
    model: str | None = None,
    dry_run: bool = False,
    max_turns: int | None = None,
    log_fn=None,
    update_state_fn=None,
) -> tuple[str, list[str]]:
    """Run the Gemini agent for one job application.

    Args:
        job: Job dict from the database (url, title, site, ...).
        cdp_port: CDP port where Chrome is listening.
        system_prompt: Full agent prompt (built by prompt.py).
        worker_id: Dashboard worker slot.
        model: Gemini model name (overrides env LLM_MODEL).
        dry_run: If True, skip the final submit click.
        max_turns: Max agent interaction turns (defaults to MAX_TURNS env or 150).
        log_fn: Callable(str) for live log lines.
        update_state_fn: Callable(**kw) for dashboard state updates.

    Returns:
        Tuple of (result_string, list_of_log_lines).
        result_string: 'applied' | 'expired' | 'captcha' | 'login_issue'
                       | 'failed:<reason>' | 'failed:turn_limit'.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment.")

    effective_model = model or os.environ.get("LLM_MODEL", "") or _DEFAULT_MODEL
    effective_max_turns = max_turns or int(os.environ.get("MAX_TURNS", str(_MAX_TURNS)))

    if log_fn is None:
        log_fn = lambda msg: log.info("[W%d] %s", worker_id, msg)
    if update_state_fn is None:
        update_state_fn = lambda **kw: None

    logs: list[str] = []
    action_count = 0

    def _log(msg: str) -> None:
        logs.append(msg)
        log_fn(msg)
        log.debug("[gemini_agent W%d] %s", worker_id, msg)

    raw_app_url = str(job.get("application_url", "")).strip()
    target_url = (
        job["url"]
        if not raw_app_url or raw_app_url.lower() in ("none", "null", "")
        else raw_app_url
    )

    effective_system = system_prompt
    if dry_run:
        effective_system += (
            "\n\n[DRY RUN MODE] Do NOT click Submit / Apply Now. "
            "When you would submit, call finish(result='APPLIED') instead."
        )

    _log(f"Starting: {job['title'][:40]} @ {job.get('site', '')} [{effective_model}]")
    result_str = "failed:turn_limit"

    prof_data = {}
    try:
        from applypilot.config import load_profile
        prof_data = load_profile()
    except Exception:
        pass
    cand_email = prof_data.get("personal", {}).get("email", "sreeragnandan25@gmail.com")
    cand_pass = prof_data.get("personal", {}).get("password", "Auto1apply2@1")

    # Prepend universal rules and ATS guidance to the system prompt
    _WORKDAY_GUIDANCE = """
== UNIVERSAL RULES (ALL JOB SITES & ATS PLATFORMS) ==
1. EXPIRED / CLOSED / PAGE NOT FOUND CHECK:
   If the target URL or landing page displays ANY of the following:
   - "The page you are looking for doesn't exist"
   - "This job is no longer available" / "Job closed" / "Position has been filled"
   - "No longer accepting applications" / "Job posting is expired"
   - An HTTP 404, Page Not Found, or generic career search redirect
   STOP IMMEDIATELY on Turn 1! DO NOT click "Search for Jobs", "Browse Jobs", or attempt to search for other roles. That target role is closed.
   Immediately call: finish(result='EXPIRED', reason='page_not_found')
   The system will automatically record it as expired in the database and advance to the next job in your queue.

2. BATCH ACTIONS FOR 5x SPEED (CRITICAL):
   To prevent session timeouts, USE `execute_actions` to fill MULTIPLE form fields on the current screen in a SINGLE TURN!
   Instead of spending 1 turn per field, batch the entire visible section:

Example batch for Workday Sign In (Step 1):
execute_actions(actions=[
  {"action": "click", "selector": "[data-automation-id='SignInWithEmailButton']"},
  {"action": "wait", "ms": 1500},
  {"action": "fill", "selector": "[data-automation-id='email']", "value": "{cand_email}"},
  {"action": "fill", "selector": "[data-automation-id='password']", "value": "{cand_pass}"},
  {"action": "click", "selector": "[data-automation-id='signInSubmitButton']"},
  {"action": "wait", "ms": 3000}
])

Example batch for contact / personal info page:
execute_actions(actions=[
  {"action": "click", "selector": "text=Address Line 1*"},
  {"action": "type_text", "text": "PADINJARE ITTAMVEETTIL, CHUNANGHAD PO"},
  {"action": "click", "selector": "text=City*"},
  {"action": "type_text", "text": "Ottapalam"},
  {"action": "click", "selector": "text=Postal Code*"},
  {"action": "type_text", "text": "679511"},
  {"action": "click", "selector": "text=Select One"},
  {"action": "click", "selector": "text=Kerala"},
  {"action": "click", "selector": "text=Phone Number*"},
  {"action": "type_text", "text": "9497034274"},
  {"action": "click", "selector": "text=Next"}
])

Example batch for questions with "Select One" dropdowns:
execute_actions(actions=[
  {"action": "click", "selector": "text=Select One"},
  {"action": "click", "selector": "ul[role='listbox'] >> text=Yes"},
  {"action": "wait", "ms": 200},
  {"action": "click", "selector": "text=Select One"},
  {"action": "click", "selector": "ul[role='listbox'] >> text=Yes"},
  {"action": "click", "selector": "text=Next"}
])

== WORKDAY FORM RULES ==
1. On the Job Posting page:
   ALWAYS click `text=Apply` -> `text=Autofill with Resume` (or `text=Apply Manually`).
   NEVER click "Sign In" in the top header navbar! That is an account menu, NOT the application!
2. For Step 1 "Create Account / Sign In":
   - NEVER click 'Back to Job Posting'!
   - If not yet on the email sign-in form, click [data-automation-id="SignInWithEmailButton"] or text=Sign in with email.
   - For SIGN IN:
     Batch fill and submit in ONE turn:
     execute_actions(actions=[
       {"action": "fill", "selector": "[data-automation-id='email']", "value": "{cand_email}"},
       {"action": "fill", "selector": "[data-automation-id='password']", "value": "{cand_pass}"},
       {"action": "click", "selector": "[data-automation-id='signInSubmitButton']"}
     ])
   - For CREATE ACCOUNT (if no account exists):
     Click [data-automation-id="createAccountLink"]
     execute_actions(actions=[
       {"action": "fill", "selector": "[data-automation-id='email']", "value": "{cand_email}"},
       {"action": "fill", "selector": "[data-automation-id='password']", "value": "{cand_pass}"},
       {"action": "fill", "selector": "[data-automation-id='verifyPassword']", "value": "{cand_pass}"},
       {"action": "click", "selector": "[data-automation-id='createAccountCheckbox']"},
       {"action": "click", "selector": "[data-automation-id='createAccountSubmitButton']"}
     ])
   - NEVER repeatedly click text=Create Account if you are already looking at the form fields!
   - If Workday displays "You need to reset your password due to an administrator request":
     Call finish(result='LOGIN_ISSUE', reason='password_reset_required').
3. For "How Did You Hear About Us?*":
   ALWAYS select LinkedIn!
   Workflow:
   {"action": "click", "selector": "#source--source"},
   {"action": "wait", "ms": 500},
   {"action": "click", "selector": "text=Job Board"},
   {"action": "wait", "ms": 1200},
   {"action": "click", "selector": "text=LinkedIn"}
   (Do NOT click LinkedIn before Job Board sub-options finish rendering!)
4. For Work Experience & Multiple Roles:
   - Enter all roles from your profile summary (Role 1: Senior Software Engineer, Role 2: Software Engineer at Converj Global).
   - If there is an 'Add' or 'Add Another' button under Work Experience, click it to add your second role!
   - NEVER click 'Delete' on cards that contain valid work experience! Only click Delete if an auto-parsed card is completely blank and blocking form submission.
   - For Dates:
     Use fill_date:
       fill_date(label='From*', month='06', year='2025')
     Or in batch:
       {"action": "fill_date", "label": "From*", "month": "06", "year": "2025"}
     For current job (Converj Global):
       Click the checkbox {"action": "click", "selector": "text=I currently work here"}, which satisfies the End Date!
     For previous role (Software Engineer, 2021-2025):
       {"action": "fill_date", "label": "From*", "month": "07", "year": "2021"},
       {"action": "fill_date", "label": "To*", "month": "06", "year": "2025"}
     For Education dates:
       {"action": "fill_date", "label": "From*", "month": "07", "year": "2017"},
       {"action": "fill_date", "label": "To*", "month": "03", "year": "2021"}
5. For Autocomplete (Degree, Field of Study, School):
   CRITICAL: Workday searches asynchronously — options take 1-2 seconds to appear.
   NEVER type and immediately press Enter! If you press Enter immediately, nothing is selected and the field stays blank!
   ALWAYS do this:
   1. Click and type query: e.g. type_text("Computer Science")
   2. WAIT 1200ms for options to render: {"action": "wait", "ms": 1200}
   3. CLICK the rendered option: {"action": "click", "selector": "[role='option']:has-text('Computer Science')"}
   For Degree:
   Click text=Degree* (or text=Select One), wait 500ms, then click text=Bachelors (or [role='option']:has-text('Bachelor')).
6. For "Select One" compliance questions:
   NEVER click the question label text expecting "Yes" to appear!
   Clicking question label does NOT open the menu.
   ALWAYS click the button showing "Select One", wait 200ms, then click "Yes" (or ul[role='listbox'] >> text=Yes).
7. CLICK field labels using visible text, e.g. click(selector='text=Job Title*') or click(selector='text=Company*').
   Do NOT guess fragile attribute selectors like input[value="..."] or layout selectors like input:below(:text("...")) if they fail.
8. Once focused, use type_text (NOT fill) to type the value — Workday inputs respond to keyboard events.
9. If an input doesn't focus directly from clicking its label, press Tab (press_key key='Tab') to move into the input field, then type_text.
10. For file uploads, use upload_file with the exact PDF path.
11. After completing all required fields (*) on a page, scroll down to find and click Next (e.g. click text=Next or Save and Continue).

== NEVER NAVIGATE AWAY OR CLICK BACK ==
Once inside the application form (URL has /apply), NEVER call navigate() to the job URL and NEVER click Back!
If any field has a validation error (e.g. Phone Number required):
Stay on the page, click the field label, type the value, and click Next!

== HANDLING RED ERROR BANNERS / VALIDATION ==
If clicking Next shows an error banner or red text (e.g. "Error: Please fix the errors below", "Required field missing"):
DO NOT call finish(result='FAILED')! That is just a validation reminder.
1. Inspect which field is flagged in red or unfilled.
2. Fill that specific missing field.
3. Click Next again!
Only call finish(result='FAILED') if you see a fatal non-recoverable error (e.g. "Position closed" or "Application rejected").

== SUBMISSION & LEARNINGS PERSISTENCE ==
- When you answer questionnaire questions or discover specific ATS selectors/patterns (e.g. restrictive covenants, contractor status, phone format, custom dropdowns), call record_learning(category, key, value) during the run, or include them in finish():
  finish(result='APPLIED', learnings=[
    {"category": "screening_answers", "key": "<question_name>", "value": "<answer>"},
    {"category": "ats_patterns.workday", "key": "<pattern_name>", "value": "<pattern_details>"}
  ])
- When you successfully submit and see a confirmation page ("Thank you" or "Application submitted"), call finish(result='APPLIED').
""".replace("{cand_email}", cand_email).replace("{cand_pass}", cand_pass)
    effective_system = _WORKDAY_GUIDANCE + "\n" + effective_system

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.connect_over_cdp(
            f"http://localhost:{cdp_port}", timeout=15000
        )
        try:
            contexts = browser.contexts
            if contexts:
                ctx = contexts[0]
                pages = ctx.pages
                page: Page = pages[0] if pages else ctx.new_page()
            else:
                ctx = browser.new_context()
                page = ctx.new_page()

            _log(f"Navigating to {target_url[:70]}")
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as nav_exc:
                _log(f"Initial navigation warning: {nav_exc}")

            history: list[dict] = []
            _last_actions: list[str] = []   # track recent action names for loop detection
            _LOOP_THRESHOLD = 3             # repeats before injecting warning

            for turn in range(effective_max_turns):
                # Prune old screenshots from history to keep context manageable
                history = _prune_history_images(history, keep_recent=3)

                screenshot_b64, ax_snapshot = _observe_page(page)
                _log(f"Turn {turn + 1}/{effective_max_turns}: calling Gemini...")

                try:
                    response = _call_gemini(
                        api_key=api_key,
                        model=effective_model,
                        system_prompt=effective_system,
                        history=history,
                        screenshot_b64=screenshot_b64,
                        ax_snapshot=ax_snapshot,
                    )
                except httpx.HTTPStatusError as exc:
                    _log(f"Gemini API error {exc.response.status_code}: {exc.response.text[:200]}")
                    result_str = "failed:llm_api_error"
                    break
                except Exception as exc:
                    _log(f"Gemini call failed: {exc}")
                    result_str = "failed:llm_error"
                    break

                candidates = response.get("candidates", [])
                if not candidates:
                    _log("Gemini returned no candidates.")
                    result_str = "failed:no_candidates"
                    break

                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])

                # Add observation to history as user turn
                history.append({"role": "user", "parts": [
                    {"text": f"[Turn {turn + 1} page state captured above]"}
                ]})
                # Add model response to history
                history.append({"role": "model", "parts": parts})

                tool_results: list[dict] = []
                done = False

                for part in parts:
                    if "text" in part:
                        txt = part["text"]
                        _log(f"Gemini: {txt[:150]}")
                        # Legacy inline RESULT: detection (fallback)
                        for line in txt.splitlines():
                            for rs in ("APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"):
                                if f"RESULT:{rs}" in line:
                                    result_str = rs.lower()
                                    done = True
                            if "RESULT:FAILED" in line:
                                idx = line.index("FAILED")
                                reason = (
                                    line[idx + 7:].strip()
                                    if len(line) > idx + 7
                                    else "unknown"
                                )
                                result_str = f"failed:{reason}"
                                done = True

                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        fname = fc.get("name", "")
                        fargs = fc.get("args", {})
                        arg_preview = ", ".join(
                            f"{k}={str(v)[:30]}" for k, v in fargs.items()
                        )
                        action_sig = f"{fname}:{fargs.get('selector', fargs.get('text', ''))}"
                        _log(f"  -> {fname}({arg_preview})")
                        action_count += 1
                        update_state_fn(
                            actions=action_count,
                            last_action=f"{fname} {str(list(fargs.values())[:1])}"[:40],
                        )

                        # Loop detection: same action repeated too many times
                        _last_actions.append(action_sig)
                        if len(_last_actions) > _LOOP_THRESHOLD * 2:
                            _last_actions = _last_actions[-(_LOOP_THRESHOLD * 2):]
                        last_n = _last_actions[-_LOOP_THRESHOLD:]
                        if len(last_n) == _LOOP_THRESHOLD and len(set(last_n)) == 1:
                            _log(f"  [LOOP DETECTED] '{fname}' repeated {_LOOP_THRESHOLD}x — injecting warning")
                            # Force a fresh snapshot and inject a warning into history
                            snap_b64, snap_ax = _observe_page(page)
                            warning_msg = (
                                f"[LOOP DETECTED] You repeated '{fname}' {_LOOP_THRESHOLD} times with the same arguments.\n"
                                f"TROUBLESHOOTING:\n"
                                f"- If trying to select an answer from a dropdown: Click 'text=Select One' FIRST to open the menu, then click 'text=Yes'. DO NOT click the question label text!\n"
                                f"- If trying to click Next: Scroll down first (scroll direction='down'), then click 'text=Next' or 'button:has-text(\"Next\")'.\n"
                                f"- If red validation errors exist: Find the field outlined in red, fill that field, then proceed.\n"
                                f"- DO NOT quit or call finish(result='FAILED') unless the application was explicitly rejected or closed."
                            )
                            history.append({"role": "user", "parts": [
                                {"inlineData": {"mimeType": "image/jpeg", "data": snap_b64}} if snap_b64 else {"text": ""},
                                {"text": warning_msg},
                            ]})
                            _last_actions.clear()  # reset after warning

                        action_result = _execute_action(page, fname, fargs, _log)
                        _log(f"     <- {action_result[:80]}")

                        if action_result.startswith("FINISH:"):
                            parts_split = (action_result + ":").split(":", 2)
                            res = parts_split[1]
                            reason = parts_split[2].rstrip(":")
                            if res == "APPLIED":
                                result_str = "applied"
                            elif res in ("EXPIRED", "CAPTCHA", "LOGIN_ISSUE"):
                                result_str = res.lower()
                            else:
                                result_str = f"failed:{reason.strip()}" if reason.strip() else "failed:unknown"
                            done = True

                        tool_results.append({
                            "functionResponse": {
                                "name": fname,
                                "response": {"result": action_result},
                            }
                        })

                if tool_results:
                    history.append({"role": "user", "parts": tool_results})

                if done:
                    break

                finish_reason = candidate.get("finishReason", "")
                if finish_reason not in ("", "STOP", "MAX_TOKENS") and not tool_results:
                    _log(f"Gemini stopped with reason: {finish_reason}")
                    break

            else:
                _log(f"Reached turn limit ({effective_max_turns}) — marking as failed:turn_limit.")
                result_str = "failed:turn_limit"

        finally:
            pass  # Chrome lifecycle managed by chrome.py / launcher.py

    return result_str, logs

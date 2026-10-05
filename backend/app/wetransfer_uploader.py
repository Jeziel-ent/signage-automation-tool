"""Upload a file to WeTransfer through its WEBSITE and return the `https://we.tl/t-...` link (the Generate ZIP modal's
"Generate WeTransfer Link").

Why a browser: WeTransfer retired its Public API in 2020 and issues no API keys, so the only way to get a link is to drive
wetransfer.com the way a person would - with Playwright, headless, using the installed Microsoft Edge (`channel="msedge"`,
so no separate Chromium download; Playwright's own Chromium is the fallback).

BE AWARE (decided by the project owner, recorded here so nobody is surprised later):
- Every run accepts WeTransfer's Terms of Service on the company's behalf (its "I agree" gate) and sends the archive -
  client artwork - to a third party.
- WeTransfer's terms may not allow automated use of the site, and the page can change at any time; when it does, a step
  below fails with a named error and a screenshot in `debug_dir` rather than hanging.
- PARTLY VERIFIED: seen live - the cookie banner ("Reject All"), the Terms "I agree" card, and (from a failed run's
  screenshot) the send panel with the file attached: e-mail fields, a "3 days" expiry button, an icon-only "..." button and
  "Transfer". The contents of the "..." menu, the submit label in link mode and where the link appears are still guesses;
  a free link transfer may also require an account or an e-mail code, which this detects and reports but cannot pass.
  Every failure writes a screenshot AND a JSON list of the page's visible controls to `debug_dir` - use those to fix it.
  The API falls back to a link served by this server when this fails (main._wt_worker).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable

# SIGNAGE_WETRANSFER_URL is for TESTING only: point a whole server at the local stand-in page
# (tests/fixtures/wetransfer_standin.html) to exercise the full flow without contacting WeTransfer.
WETRANSFER_URL = os.environ.get("SIGNAGE_WETRANSFER_URL", "https://wetransfer.com/")
LINK_RE = re.compile(r"https://we\.tl/t-[A-Za-z0-9]+")
PERCENT_RE = re.compile(r"(\d{1,3})\s?%")
# screens this cannot get past (an account, a paid plan)
BLOCKER_RE = re.compile(r"log in to continue|sign up to (send|get)|upgrade to|limit reached|reached your limit", re.I)
# a screen asking for the code WeTransfer e-mailed to the sender: the PERSON types it in the modal (`ask_code`)
CODE_RE = re.compile(r"verification code|verify your e-?mail|enter (the|your) (\d-digit )?code|(\d|six)-digit code|"
                     r"we('ve| have)? (just )?sent (you )?a code|check your (e-?mail|inbox)|confirm your e-?mail", re.I)
# WeTransfer's hint when "Get a link" is pressed without a sender address (seen live, 2026-09-28)
EMAIL_NEEDED_RE = re.compile(r"we ask for your email", re.I)
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")  # NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes
SUBMIT_TESTID = "uploaderForm-transfer-button"      # the "Transfer" / "Get a link" button (seen live)
MAX_CODE_ATTEMPTS = 3
NAV_STEP_MS = 15_000         # the longest any one on-page step (consent, link mode, submit) may take before it fails
DEFAULT_MAX_GB = 3.0          # WeTransfer's free-transfer size limit when this was written; SIGNAGE_WETRANSFER_MAX_GB overrides


class WeTransferError(RuntimeError):
    """A named step failed; the message says which, and where its screenshot is."""


def max_bytes() -> int:
    try:
        gb = float(os.environ.get("SIGNAGE_WETRANSFER_MAX_GB", DEFAULT_MAX_GB))
    except ValueError:
        gb = DEFAULT_MAX_GB
    return int(gb * 1e9)


def check_available() -> None:
    """ImportError if Playwright is not installed for this Python (the API turns it into a 503 with the pip command)."""
    import playwright.sync_api  # noqa: F401


def extract_link(*texts: str | None) -> str | None:
    """The first `https://we.tl/t-...` link in any of `texts` (page text, input values, the clipboard)."""
    for t in texts:
        m = LINK_RE.search(t or "")
        if m:
            return m.group(0)
    return None


def upload_timeout_s(size_bytes: int) -> float:
    """How long to wait for the upload: 3 minutes plus the file at a slow 0.5 MB/s."""
    return 180.0 + size_bytes / 500_000


def valid_email(address: str | None) -> bool:
    return bool(address) and len(address) <= 254 and bool(EMAIL_RE.fullmatch(address.strip()))


def upload_zip_to_wetransfer(zip_file_path: str, sender_email: str | None = None, on_step: Callable[..., None] | None = None,
                             debug_dir: Path | None = None, headless: bool = True,
                             ask_code: Callable[[str | None], str | None] | None = None) -> str:
    """Upload `zip_file_path` as a WeTransfer LINK transfer and return the link.

    `sender_email` (the person's own address, typed in the modal) goes into the panel's "Your email" field, which
    WeTransfer requires for a link transfer ("We ask for your email to keep the community safe", seen live). If WeTransfer
    then asks for a code it e-mailed to that address, `ask_code(error_or_None)` is called - it blocks until the person
    types the code in the modal and returns it (None = gave up / timed out); a rejected code asks again, up to
    MAX_CODE_ATTEMPTS. The browser stays open on this thread meanwhile. `on_step(name, progress=None)` reports progress.
    Raises WeTransferError naming the step that failed."""
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    step = on_step or (lambda *a, **k: None)
    if not valid_email(sender_email):
        raise WeTransferError("a valid sender e-mail address is required - WeTransfer asks for one before it makes a link")
    sender_email = sender_email.strip()
    path = Path(zip_file_path).resolve()
    if not path.is_file():
        raise WeTransferError(f"the ZIP to upload does not exist: {path}")
    size = path.stat().st_size
    if size > max_bytes():
        raise WeTransferError(f"{size / 1e9:.2f} GB is over the {max_bytes() / 1e9:.0f} GB limit set for WeTransfer")
    current = {"name": "launch"}

    def at(name: str, progress: float | None = None) -> None:
        current["name"] = name
        step(name, progress)

    with sync_playwright() as p:
        at("launch")
        try:
            browser = p.chromium.launch(channel="msedge", headless=headless)
        except Exception:
            browser = p.chromium.launch(headless=headless)        # Playwright's own Chromium, if installed
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US",
                                  permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        try:
            at("open")
            page.goto(WETRANSFER_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)

            at("consent")
            # cookie banner (seen live): reject tracking; only a banner without a reject option gets "Accept"
            if not _click_if_present(page, [("button", r"^(reject all|reject|decline( all)?)$")], timeout=6000):
                _click_if_present(page, [("button", r"^(accept( all)?|i accept|ok|got it)$")], timeout=2000)
            _click_if_present(page, [("button", r"^i agree$")], timeout=6000)               # Terms gate (seen live)
            _raise_if_blocked(page)

            at("add_file")
            file_input = page.locator("input[type=file]").first
            file_input.wait_for(state="attached", timeout=20_000)
            file_input.set_input_files(str(path))
            page.wait_for_timeout(1500)

            at("link_mode")
            _set_link_mode(page)
            _raise_if_blocked(page)

            at("email")
            _fill_sender_email(page, sender_email)

            at("submit")
            submit = page.get_by_test_id(SUBMIT_TESTID)
            if submit.count() and submit.first.is_visible():
                submit.first.click(timeout=NAV_STEP_MS)
            elif not _click_if_present(page, [("button", r"^(get a link|get link|transfer)$")], timeout=NAV_STEP_MS):
                raise WeTransferError("could not find the 'Get a link' / 'Transfer' button")
            page.wait_for_timeout(1500)
            if EMAIL_NEEDED_RE.search(_body(page)) and not _email_value(page):
                raise WeTransferError("WeTransfer still asks for the sender e-mail - the 'Your email' field did not keep it")

            at("uploading", 0)
            deadline = time.time() + upload_timeout_s(size)
            link, codes_sent, code_error = None, 0, None
            while time.time() < deadline:
                _raise_if_blocked(page)
                body = _body(page)
                values = page.eval_on_selector_all("input, textarea", "els => els.map(e => e.value).join(' ')")
                link = extract_link(body, values)
                if link:
                    break
                if CODE_RE.search(body) and _code_inputs(page):
                    if ask_code is None:
                        raise WeTransferError("WeTransfer asks for the code it e-mailed to the sender, and nobody can type it here")
                    if codes_sent >= MAX_CODE_ATTEMPTS:
                        raise WeTransferError(f"WeTransfer did not accept the e-mailed code after {MAX_CODE_ATTEMPTS} tries")
                    at("requires_otp")
                    code = ask_code(code_error)
                    if not code:
                        raise WeTransferError("no verification code was entered in time")
                    at("verifying")
                    _enter_code(page, code)
                    codes_sent += 1
                    page.wait_for_timeout(2500)
                    code_error = ("WeTransfer did not accept that code - check the latest e-mail and try again"
                                  if CODE_RE.search(_body(page)) and _code_inputs(page) else None)
                    at("uploading", 0)
                    continue
                m = PERCENT_RE.findall(body)
                if m:
                    at("uploading", min(99, max(int(v) for v in m)))
                page.wait_for_timeout(1000)
            if not link:
                raise WeTransferError("no we.tl link appeared before the upload timed out")
            at("done", 100)
            return link
        except WeTransferError as e:
            raise WeTransferError(f"step '{current['name']}': {e}{_diagnose(page, debug_dir, current['name'])}") from None
        except PWTimeout as e:
            raise WeTransferError(f"step '{current['name']}' timed out ({str(e).splitlines()[0]})"
                                  f"{_diagnose(page, debug_dir, current['name'])}") from None
        finally:
            ctx.close()
            browser.close()


def _body(page) -> str:
    try:
        return page.inner_text("body", timeout=10_000)
    except Exception:
        return ""


def _email_value(page) -> str:
    try:
        return page.locator("input[type=email]").first.input_value(timeout=3000).strip()
    except Exception:
        return ""


def _fill_sender_email(page, address: str) -> None:
    """The panel's "Your email" field (seen live: `input[type=email]` labelled "Your email"), read back after typing."""
    field = page.get_by_label(re.compile(r"^your e-?mail$", re.I))
    if not (field.count() and field.first.is_visible()):
        field = page.locator("input[type=email], input[name*=email i], input[placeholder*=email i]")
    visible = [field.nth(i) for i in range(field.count()) if field.nth(i).is_visible()]
    if not visible:
        raise WeTransferError("could not find the 'Your email' field")
    box = visible[0]
    box.click(timeout=5000)
    box.fill(address, timeout=5000)
    box.press("Tab")
    if box.input_value().strip() != address:
        raise WeTransferError("typed the sender e-mail but the field did not keep it")


_CODE_HINT = re.compile(r"code|otp|one-time|digit|numeric|verif", re.I)


def _code_boxes(page) -> list:
    """Visible inputs that take a code: a row of one-character boxes, or one box whose name/label/placeholder says so."""
    out = []
    for h in page.query_selector_all("input"):
        try:
            if not h.is_visible() or (h.get_attribute("type") or "text").lower() not in ("text", "tel", "number", "password"):
                continue
            hint = " ".join(filter(None, [h.get_attribute(a) for a in
                                          ("name", "id", "autocomplete", "aria-label", "placeholder", "inputmode")]))
            if h.get_attribute("maxlength") == "1" or _CODE_HINT.search(hint):
                out.append(h)
        except Exception:
            continue
    return out


def _code_inputs(page) -> int:
    return len(_code_boxes(page))


def _enter_code(page, code: str) -> None:
    """Type the e-mailed code the person entered (upper-cased letters and digits): one box, or a row of one-character boxes (type into the first - such rows
    move focus themselves; if not, each box is filled). Then press Verify/Continue if shown, else Enter."""
    code = re.sub(r"[^A-Za-z0-9]", "", code).upper()          # letters and digits, e.g. 953GYV
    boxes = _code_boxes(page)
    if not boxes:
        raise WeTransferError("could not find where to type the verification code")
    if len(boxes) == 1:
        boxes[0].fill("")
        boxes[0].fill(code)
    else:
        boxes[0].click()
        page.keyboard.type(code, delay=60)
        if "".join(b.input_value() for b in boxes) != code[:len(boxes)]:
            for b, ch in zip(boxes, code):
                b.fill(ch)
    if not _click_if_present(page, [("button", r"^(verify|continue|confirm|submit|next)$")], timeout=2000):
        page.keyboard.press("Enter")


# The send panel as seen live (screenshot 2026-09-28, file attached): "Email to" / "Your email" / "Title" fields, then a
# "3 days" expiry button, an ICON-ONLY "..." button beside it, and "Transfer" below both. The "..." button's accessible
# name did not match "more options|options" - so it is found by name first and, failing that, by position: the icon-only
# button nearest above the Transfer button, inside the same panel.
_ICON_BUTTON_NEAR_SUBMIT = """() => {
  const go = [...document.querySelectorAll('button')].find(b => /^(transfer|get a link|get link)$/i.test(b.innerText.trim()));
  if (!go) return null;
  const gb = go.getBoundingClientRect();
  let panel = go.parentElement;
  for (let i = 0; i < 8 && panel; i++, panel = panel.parentElement) {
    const cands = [...panel.querySelectorAll('button')].filter(b => {
      if (b === go || b.innerText.trim() || !b.offsetParent) return false;
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.bottom <= gb.top + 4 && gb.top - r.bottom < 120;     // just above the submit button
    });
    if (cands.length) {
      cands.sort((a, b) => (gb.top - a.getBoundingClientRect().bottom) - (gb.top - b.getBoundingClientRect().bottom)
                           || b.getBoundingClientRect().right - a.getBoundingClientRect().right);
      return cands[0];
    }
  }
  return null;
}"""
LINK_CHOICES = [("radio", r"\blink\b"), ("menuitemradio", r"\blink\b"), ("menuitem", r"\blink\b"), ("option", r"\blink\b"),
                ("tab", r"\blink\b"), ("switch", r"\blink\b"), ("button", r"get (a |transfer )?link|^link$"),
                ("label", r"get (a |transfer )?link")]


def _link_mode_active(page) -> bool:
    """Already a link transfer: the submit button says so, or a link choice is checked."""
    try:
        if page.get_by_role("button", name=re.compile(r"^(get a link|get link)$", re.I)).count():
            return True
        return bool(page.evaluate("""() => [...document.querySelectorAll('[role=radio],[role=menuitemradio],input[type=radio]')]
            .some(e => /\\blink\\b/i.test((e.innerText || e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) || ''))
                       && (e.checked || e.getAttribute('aria-checked') === 'true'))"""))
    except Exception:
        return False


def _set_link_mode(page) -> None:
    """Switch the transfer from e-mail to link: a visible link choice if there is one, else open the options menu
    (by name, else the icon-only button above Transfer) and pick it there."""
    if _link_mode_active(page):
        return
    if _click_if_present(page, LINK_CHOICES, timeout=1500):
        return
    # anchored: the live panel also has an "Add more" (files) button, which a loose /more/ matched first
    opened = _click_if_present(page, [("button", r"^(more|more options|options|transfer options|transfer settings|settings|"
                                                 r"menu|open menu|delivery options|transfer type)$")], timeout=2000)
    if not opened:
        try:
            el = page.evaluate_handle(_ICON_BUTTON_NEAR_SUBMIT).as_element()
            if el is not None:
                el.click(timeout=5000)
                opened = True
        except Exception:
            opened = False
    if not opened:
        raise WeTransferError("could not find the transfer options ('...') button")
    page.wait_for_timeout(600)
    if not _click_if_present(page, LINK_CHOICES, timeout=NAV_STEP_MS):
        raise WeTransferError("opened the transfer options but found no 'link' choice in them")
    page.wait_for_timeout(600)
    # a menu may stay open over the submit button; Escape closes it without undoing the choice
    if not page.get_by_role("button", name=re.compile(r"^(get a link|get link|transfer)$", re.I)).first.is_visible():
        page.keyboard.press("Escape")


def _click_if_present(page, candidates: list[tuple[str, str]], timeout: int = 5000) -> bool:
    """Click the first visible element matching (role, name regex); False if none shows up within `timeout` ms."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for role, pattern in candidates:
            loc = page.get_by_role(role, name=re.compile(pattern, re.I)) if role != "label" \
                else page.get_by_text(re.compile(pattern, re.I))
            try:
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    return True
            except Exception:
                pass
        page.wait_for_timeout(300)
    return False


def _raise_if_blocked(page) -> None:
    try:
        text = page.inner_text("body", timeout=5000)
    except Exception:
        return
    m = BLOCKER_RE.search(text)
    if m:
        raise WeTransferError(f"WeTransfer is asking for something this cannot do automatically (\"{m.group(0)}\" - "
                              "an account, an e-mail code or a paid plan)")


_VISIBLE_CONTROLS = """() => [...document.querySelectorAll(
    'button,[role=button],[role=radio],[role=menuitem],[role=menuitemradio],[role=option],[role=tab],[role=switch],input,label,a')]
  .filter(e => e.offsetParent || e.getClientRects().length)
  .slice(0, 150)
  .map(e => { const r = e.getBoundingClientRect(); return {
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), type: e.getAttribute('type'),
    text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80), aria: e.getAttribute('aria-label'),
    title: e.getAttribute('title'), testid: e.getAttribute('data-testid'), checked: e.getAttribute('aria-checked'),
    rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] }; })"""


def _diagnose(page, debug_dir: Path | None, name: str) -> str:
    """Screenshot plus a JSON list of the page's visible controls (text, aria-label, role, test id, position), so a
    WeTransfer page change can be fixed from what the page really offers instead of guessed again."""
    note = _screenshot(page, debug_dir, name)
    if debug_dir is None:
        return note
    try:
        import json
        controls = page.evaluate(_VISIBLE_CONTROLS)
        out = Path(debug_dir) / f"wetransfer_{time.strftime('%Y%m%d_%H%M%S')}_{name}_controls.json"
        out.write_text(json.dumps({"url": page.url, "controls": controls}, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return note


def _screenshot(page, debug_dir: Path | None, name: str) -> str:
    if debug_dir is None:
        return ""
    try:
        debug_dir = Path(debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        shot = debug_dir / f"wetransfer_{time.strftime('%Y%m%d_%H%M%S')}_{name}.png"
        page.screenshot(path=str(shot), full_page=True)
        return f" (screenshot: {shot})"
    except Exception:
        return ""

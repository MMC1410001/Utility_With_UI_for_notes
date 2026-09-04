"""Fetch a course's transcripts through your own logged-in browser.

This reimplements what the Udemy Transcript Extractor extension does, because
a Chrome extension has no external RPC surface a script can call. The
important property is preserved: the requests run inside a real browser
session that *you* logged into, so no credential is ever extracted, stored, or
passed to this tool.

Endpoints (undocumented, Udemy-internal - they can change without notice):
    /api-2.0/courses/{id}/subscriber-curriculum-items/
    /api-2.0/users/me/subscribed-courses/{id}/lectures/{lecture_id}/
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import coursetree
from .vtt import vtt_to_text

# Checking for login costs nothing now (cookies are read locally), but the
# browser is left completely alone while a challenge is on screen.
LOGIN_POLL_SECONDS = 3

# Only `access_token` means authenticated. `dj_session_id` is set for
# anonymous visitors too - treating it as a login signal makes the fetch march
# on before the user is in, and every API call then 403s.
SESSION_COOKIES = ("access_token",)

# Cloudflare's interstitial. While this is up we make no requests at all -
# polling an API every couple of seconds is itself bot-shaped traffic and
# feeds the very check the user is trying to clear.
CHALLENGE_MARKERS = (
    "challenges.cloudflare.com", "cf-challenge", "just a moment",
    "verify you are human", "checking your browser",
)
LOGIN_TIMEOUT_SECONDS = 600

CURRICULUM = (
    "https://www.udemy.com/api-2.0/courses/{course_id}/subscriber-curriculum-items/"
    "?page_size=1000"
    "&fields[lecture]=id,title,object_index,asset"
    "&fields[chapter]=id,title,object_index"
    "&fields[asset]=captions,title,asset_type"
    "&fields[caption]=id,locale_id,url,source"
)
LECTURE = (
    "https://www.udemy.com/api-2.0/users/me/subscribed-courses/{course_id}"
    "/lectures/{lecture_id}/?fields[lecture]=asset&fields[asset]=captions"
    "&fields[caption]=id,locale_id,url,source"
)
ME = "https://www.udemy.com/api-2.0/contexts/me/?header=True"
SUBSCRIBED = (
    "https://www.udemy.com/api-2.0/users/me/subscribed-courses/"
    "?page_size=100&fields[course]=id,title,url"
)
LOGIN_URL = "https://www.udemy.com/join/login-popup/"

# Playwright's bundled Chromium trips Cloudflare's bot check, which then loops
# the "are you human?" challenge forever. Real Chrome with the automation
# giveaways removed passes it.
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
]
DROP_ARGS = ["--enable-automation", "--disable-extensions"]

# navigator.webdriver is the single most-checked signal; the rest of these
# round out a headed-Chrome fingerprint.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""

# The on-disk layout is shared with the server's extension-import path, so it
# lives in one place. Re-exported here because `ingest` and older callers
# import these names from this module.
NO_TRANSCRIPT = coursetree.NO_TRANSCRIPT
SOURCE_MARKER = coursetree.SOURCE_MARKER
HEADER = coursetree.HEADER

_safe = coursetree.safe
_pick_caption = coursetree.pick_caption
_slug = coursetree.course_slug


class FetchError(RuntimeError):
    pass


def _course_id_from_api(page, url: str) -> int | None:
    """Resolve the course by slug against the courses you are enrolled in.

    Preferred over scraping the page: Udemy sends enrolled users to a
    different layout than the sales page, so whichever DOM attribute carried
    the id a moment ago may not be there after signing in.
    """
    slug = _slug(url)
    if not slug:
        return None

    next_url = SUBSCRIBED
    seen = 0
    while next_url:
        try:
            payload = _api(page, next_url)
        except FetchError:
            return None
        for course in payload.get("results", []):
            seen += 1
            if _slug(course.get("url") or "") == slug:
                return int(course["id"])
        next_url = payload.get("next")
    if seen:
        print(f"    checked {seen} enrolled course(s), no slug match for '{slug}'")
    return None


def _course_id(page) -> int:
    """Read the course id out of the loaded page.

    Udemy has moved this around over the years, so try the known carriers in
    order rather than betting on one.
    """
    strategies = [
        "document.body?.dataset?.clpCourseId",
        "document.querySelector('[data-clp-course-id]')?.dataset?.clpCourseId",
        "document.querySelector('.ud-component--course-taking--app')"
        "?.dataset?.moduleArgs && JSON.parse(document.querySelector("
        "'.ud-component--course-taking--app').dataset.moduleArgs).courseId",
        "window.UD?.course?.id",
    ]
    for js in strategies:
        try:
            value = page.evaluate(f"() => {js}")
        except Exception:  # noqa: BLE001 - a failing probe just means "try the next"
            continue
        if value:
            return int(value)

    m = re.search(r'"course_id"\s*:\s*(\d+)|data-clp-course-id="(\d+)"', page.content())
    if m:
        return int(m.group(1) or m.group(2))

    m = re.search(r"/learn/lecture/(\d+)", page.url)
    if m:
        try:
            detail = _api(
                page,
                "https://www.udemy.com/api-2.0/lectures/"
                f"{m.group(1)}/?fields[lecture]=course",
            )
            course = detail.get("course") or {}
            if course.get("id"):
                return int(course["id"])
        except FetchError:
            pass

    raise FetchError(
        "could not determine the course id from the page. Make sure the URL "
        "points at a course you are enrolled in, and that it finished loading."
    )


def _course_title(page) -> str:
    for js in [
        "document.querySelector('h1')?.textContent",
        "document.title.split('|')[0]",
    ]:
        try:
            value = page.evaluate(f"() => {js}")
        except Exception:  # noqa: BLE001
            continue
        if value and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


# Requests must originate *inside* the page. Playwright's own HTTP client
# shares cookies but not Chrome's TLS fingerprint or header order, so
# Cloudflare answers it with a "Just a moment..." challenge and a 403. An
# in-page fetch is the site's own XHR and passes cleanly - this is effectively
# what the browser extension does, and why an extension was the right shape
# for this job in the first place.
IN_PAGE_FETCH = """async (u) => {
  const r = await fetch(u, {
    credentials: 'include',
    headers: {'Accept': 'application/json, text/plain, */*'},
  });
  return {status: r.status, body: await r.text()};
}"""


def _api(page, url: str) -> dict:
    try:
        res = page.evaluate(IN_PAGE_FETCH, url)
    except Exception as exc:  # noqa: BLE001 - page navigated mid-call
        raise FetchError(f"request failed for {url.split('?')[0]}: {exc}") from exc

    status, body = res.get("status"), res.get("body") or ""
    if status == 403:
        raise FetchError(
            f"Udemy returned 403 for {url.split('?')[0]}\n"
            "  The session is not authenticated for this course, or you are not\n"
            "  enrolled in it. Check the browser is signed in to the right account."
        )
    if status != 200:
        raise FetchError(f"{url.split('?')[0]} returned HTTP {status}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(
            f"{url.split('?')[0]} did not return JSON "
            f"(got {body[:80]!r})"
        ) from exc


def _fetch_text(page, url: str) -> str | None:
    """Download a caption file.

    Captions live on S3, which is not behind Cloudflare, so Playwright's HTTP
    client is fine here and avoids cross-origin reads from the page.
    """
    try:
        response = page.request.get(url)
        return response.text() if response.ok else None
    except Exception:  # noqa: BLE001
        return None


def _logged_in(page) -> str | None:
    """Return the signed-in user's display name, or None."""
    try:
        user = (_api(page, ME).get("header") or {}).get("user") or {}
    except Exception:  # noqa: BLE001 - not signed in, or challenged
        return None
    if not user.get("id"):
        return None
    return user.get("display_name") or user.get("name") or "your account"


def _has_session(context) -> bool:
    """Login check that costs zero network requests - just reads cookies.

    Reads every cookie and filters by name and domain rather than asking
    Playwright to URL-match: `access_token` is set host-only on
    www.udemy.com, and URL matching quietly missed it.
    """
    try:
        cookies = context.cookies()
    except Exception:  # noqa: BLE001
        return False
    for c in cookies:
        if c.get("name") in SESSION_COOKIES and "udemy.com" in (c.get("domain") or ""):
            return True
    return False


def _on_challenge(page) -> bool:
    try:
        blob = (page.url + " " + (page.title() or "")).lower()
        if any(m in blob for m in CHALLENGE_MARKERS):
            return True
        return page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0
    except Exception:  # noqa: BLE001 - mid-navigation
        return False


def _wait_for_login(page, course_url: str) -> str:
    """Block until the user signs in, then carry on by itself.

    Waiting is done by reading cookies locally rather than calling the API,
    and pauses entirely while a Cloudflare challenge is on screen, so nothing
    this tool does can make that challenge harder to clear.
    """
    import time

    context = page.context
    if _has_session(context):
        who = _logged_in(page) or "you"
        print(f"  already signed in as {who}")
        return who

    print("\n" + "=" * 68)
    print("  SIGN IN TO UDEMY IN THE BROWSER WINDOW")
    print("=" * 68)
    print("  Log in however you normally do - password, Google, SSO, 2FA.")
    print("  If Cloudflare asks you to confirm you are human, do that first;")
    print("  this tool stays completely idle until you are through.")
    print("  Nothing to type here: the download starts on its own.")
    print(f"  (waiting up to {LOGIN_TIMEOUT_SECONDS // 60} minutes)\n")

    deadline = time.time() + LOGIN_TIMEOUT_SECONDS
    ticks = 0
    announced_challenge = False

    while time.time() < deadline:
        if _on_challenge(page):
            if not announced_challenge:
                print("    Cloudflare check on screen - holding off, take your time")
                announced_challenge = True
            time.sleep(5)
            continue

        if announced_challenge:
            print("    challenge cleared")
            announced_challenge = False

        if _has_session(context):
            # The cookie is a cheap gate; the API is the authority. If it does
            # not confirm a user, keep waiting rather than marching into 403s.
            who = _logged_in(page)
            if who:
                print(f"\n  signed in as {who} - continuing automatically\n")
                page.goto(course_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2_000)
                return who

        ticks += 1
        if ticks % 20 == 0:
            left = int(deadline - time.time())
            try:
                n = len([c for c in context.cookies()
                         if "udemy.com" in (c.get("domain") or "")])
            except Exception:  # noqa: BLE001
                n = -1
            print(f"    still waiting... {left // 60}m {left % 60}s left "
                  f"({n} udemy cookies, none of {'/'.join(SESSION_COOKIES)} yet)")
        time.sleep(LOGIN_POLL_SECONDS)

    raise FetchError(
        "timed out waiting for Udemy login. Re-run and sign in; the browser "
        "profile is kept, so once you're in it stays that way."
    )


def browser_profile_dir() -> Path:
    """Where the signed-in Chrome profile lives.

    Kept out of the transcripts folder: that directory holds course content
    you may want to move, copy or delete, and a browser profile carrying a
    live Udemy session has no business travelling with it.
    """
    import os

    configured = os.environ.get("NOTESGEN_BROWSER_PROFILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".notesgen" / "browser-profile"


def _launch(pw, profile: Path, headless: bool, cdp_port: int | None):
    """Return (context, close_fn).

    Two ways in, most reliable last:
      - real Chrome driven by us, automation signals stripped
      - attach to a Chrome *you* started, which is indistinguishable from
        normal browsing because it is normal browsing
    """
    if cdp_port:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context, browser.close

    for channel in ("chrome", None):  # real Chrome first, bundled as fallback
        try:
            context = pw.chromium.launch_persistent_context(
                str(profile),
                headless=headless,
                channel=channel,
                args=STEALTH_ARGS,
                ignore_default_args=DROP_ARGS,
                viewport={"width": 1400, "height": 900},
            )
            if channel:
                print("  using your installed Google Chrome")
            else:
                # Bundled Chromium needs the fingerprint patch; real Chrome
                # does not, and patching it is itself a detectable signal.
                print("  using bundled Chromium (Cloudflare may challenge repeatedly)")
                context.add_init_script(STEALTH_JS)
            return context, context.close
        except Exception as exc:  # noqa: BLE001 - Chrome not installed; try the next
            last = exc
    raise FetchError(f"could not start a browser: {last}")


def fetch(
    url: str,
    workdir: Path,
    *,
    headless: bool = False,
    cdp_port: int | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "fetching by URL needs Playwright:\n"
            "    python3 -m notesgen setup --extra udemy\n"
            "Or download the transcripts with the Udemy Transcript Extractor "
            "extension and pass the .zip to --input instead."
        ) from exc

    profile = browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context, close = _launch(pw, profile, headless, cdp_port)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            print("  opening browser...")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            _wait_for_login(page, url)

            course_id = _course_id_from_api(page, url) or _course_id(page)
            items = _api(page, CURRICULUM.format(course_id=course_id)).get("results", [])
            course = _course_title(page) or f"course-{course_id}"
            lectures = sum(1 for i in items if i.get("_class") == "lecture")
            print(f"  {course}")
            print(f"  course id {course_id}, {lectures} lectures\n")

            root = _write_tree(page, course_id, course, items, workdir)
        finally:
            close()

    (root / SOURCE_MARKER).write_text(_slug(url) or url, encoding="utf-8")
    _zip(root, workdir / f"{root.name}-transcripts.zip")
    return root


def _write_tree(page, course_id: int, course: str, items: list[dict], workdir: Path) -> Path:
    def report(chapter_no: int, lecture_no: int, title: str, absent: bool) -> None:
        print(f"    {'--' if absent else 'ok'}  {chapter_no:02d}.{lecture_no:02d} {title[:56]}")

    root, written, missing = coursetree.write_tree(
        course,
        items,
        workdir,
        lambda item: _lecture_text(page, course_id, item),
        on_lecture=report,
    )
    print(f"\n  {written} lecture(s) with transcripts, {missing} without")
    return root


def _lecture_text(page, course_id: int, item: dict) -> str | None:
    asset = item.get("asset") or {}
    captions = asset.get("captions")
    # The curriculum call usually inlines captions; fall back to the per-lecture
    # endpoint when it doesn't.
    if captions is None:
        try:
            detail = _api(page, LECTURE.format(course_id=course_id, lecture_id=item["id"]))
            captions = (detail.get("asset") or {}).get("captions") or []
        except Exception:  # noqa: BLE001
            return None

    url = _pick_caption(captions or [])
    if not url:
        return None
    raw = _fetch_text(page, url)
    if not raw:
        return None
    return vtt_to_text(raw) or None


_write_combined = coursetree.write_combined


def _zip(root: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root.parent))
    print(f"  saved {archive}")

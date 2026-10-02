#!/usr/bin/env python3
"""Scraping Eagle · the `scrape` command.

One entry point for web scraping: platform routes (video, tweets, Reddit, GitHub, feeds, search)
and a fetch engine that escalates on its own from plain HTTP to a stealth browser.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html as htmllib
import importlib.util
import io
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "1.0.0"
HOME_DIR = Path(os.environ.get("SCRAPING_EAGLE_HOME") or Path.home() / ".scraping-eagle")
OUT_DIR = HOME_DIR / "out"
PROFILES_DIR = HOME_DIR / "profiles"
DEFAULT_MAX_CHARS = 40_000
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
TOOL_UA = f"scraping-eagle/{VERSION}"
EXA_URL = "https://mcp.exa.ai/mcp"
JINA_URL = "https://r.jina.ai/"
IGNORED_TAGS = ("script", "style", "noscript", "svg", "template", "iframe")

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_NO_ENGINE, EXIT_USAGE = 0, 1, 2, 3, 64
STATE = {"quiet": False, "verbose": False}


class ScrapeError(Exception):
    def __init__(self, msg: str, code: int = EXIT_ERROR):
        super().__init__(msg)
        self.code = code


def log(msg: str) -> None:
    if not STATE["quiet"]:
        print(f"[scrape] {msg}", file=sys.stderr, flush=True)


# ── utilities ────────────────────────────────────────────────────────────────


def normalize_url(raw: str) -> str:
    u = raw.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        p = urllib.parse.urlsplit(u)
        valid = p.scheme in ("http", "https") and bool(p.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ScrapeError(f"Invalid URL: {raw}", EXIT_USAGE)
    return u


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def origin_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def host_matches(url: str, *domains: str) -> bool:
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in domains)


def site_of(url: str) -> str:
    h = host_of(url)
    return h[4:] if h.startswith("www.") else h


def creds_allowed(url: str, a) -> bool:
    """User cookies and headers only travel to the site of the first URL and its subdomains."""
    site = getattr(a, "auth_site", None)
    return bool(site) and host_matches(url, site)


def compile_rx(pattern, flag: str):
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ScrapeError(f"Invalid regular expression in {flag} '{pattern}': {e}", EXIT_USAGE)


def slug(url: str, maxlen: int = 80) -> str:
    p = urllib.parse.urlsplit(url)
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{p.netloc}{p.path}".strip("/")).strip("-")
    return (s or "index")[:maxlen]


def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip()


def strip_html(s: str) -> str:
    return clean(htmllib.unescape(re.sub(r"<[^>]+>", " ", s or "")))


def fmt_ts(sec: float) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_headers(items) -> dict:
    out = {}
    for h in items or []:
        if ":" not in h:
            raise ScrapeError(f"Invalid header (use 'Key: value'): {h}", EXIT_USAGE)
        k, v = h.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def parse_cookies(s: str) -> dict:
    out = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def proxy_of(a) -> str | None:
    return getattr(a, "proxy", None) or os.environ.get("SCRAPE_PROXY") or None


_LOCALE_RX = re.compile(r"^([a-z]{2,3})[_-]([A-Z]{2})")
_LOCALE_CACHE: list = []


def system_locale() -> str | None:
    """System language as a browser would announce it (es-ES), so the http and stealth tiers agree."""
    if _LOCALE_CACHE:
        return _LOCALE_CACHE[0]
    candidates = [os.environ.get("SCRAPE_LOCALE", "")]
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=5).stdout
            candidates += re.findall(r"[A-Za-z]{2,3}[-_][A-Z]{2}", out)[:1]
        except (OSError, subprocess.SubprocessError):
            pass
    candidates += [os.environ.get("LC_ALL", ""), os.environ.get("LANG", "")]
    found = None
    for c in candidates:
        m = _LOCALE_RX.match(c or "")
        if m:
            found = f"{m.group(1)}-{m.group(2)}"
            break
    _LOCALE_CACHE.append(found)
    return found


def accept_language(a) -> str | None:
    loc = getattr(a, "locale", None) or system_locale()
    if not loc:
        return None
    lang = loc.split("-")[0]
    return f"{loc},{lang};q=0.9" + ("" if lang == "en" else ",en;q=0.8")


def run(cmd: list, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def rows_to_csv(rows: list) -> str:
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("; ".join(map(str, v)) if isinstance(v, list) else v) for k, v in r.items()})
    return buf.getvalue()


def with_ext(name: str, ext: str) -> str:
    return name if name.lower().endswith("." + ext) else f"{name}.{ext}"


def save_full(text: str, name: str, ext: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{with_ext(name, ext)}"
    path.write_text(text, encoding="utf-8")
    return path


def emit(text: str, a, name: str, ext: str = "md") -> None:
    """Print or save. When output is truncated, the full content is written to disk."""
    out = getattr(a, "output", None)
    if out:
        path = Path(out).expanduser()
        if path.is_dir() or str(out).endswith(("/", os.sep)):
            path = path / with_ext(name, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Saved: {path} ({len(text):,} characters)")
        return
    limit = getattr(a, "max_chars", DEFAULT_MAX_CHARS)
    if limit and len(text) > limit:
        path = save_full(text, name, ext)
        sys.stdout.write(text[:limit].rstrip() + "\n")
        sys.stdout.write(
            f"\n[scrape] Output truncated to {limit:,} of {len(text):,} characters. Full content: {path}\n"
        )
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


# ── minimal HTTP (stdlib) and remote readers ─────────────────────────────────


def _ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_call(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: int = 30, ua: str = UA):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ScrapeError(f"Cannot reach {host_of(url)}: {e}")


def exa_call(tool: str, arguments: dict, timeout: int = 60) -> str:
    """Call Exa's public MCP endpoint (no key needed) over plain HTTP."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if os.environ.get("EXA_API_KEY"):
        headers["x-api-key"] = os.environ["EXA_API_KEY"]
    status, body = http_call(EXA_URL, data=json.dumps(payload).encode(), headers=headers, timeout=timeout, ua=TOOL_UA)
    text = body.decode("utf-8", "replace")
    if status != 200:
        raise ScrapeError(f"Exa returned HTTP {status}: {text[:200]}")
    msg = None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                msg = json.loads(line[5:].strip())
            except ValueError:
                continue
    if msg is None:
        try:
            msg = json.loads(text)
        except ValueError:
            raise ScrapeError("Unrecognised response from Exa")
    if "error" in msg:
        raise ScrapeError(f"Exa: {msg['error'].get('message', msg['error'])}")
    result = msg.get("result") or {}
    parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    out = "\n".join(parts).strip()
    if result.get("isError"):
        raise ScrapeError(f"Exa: {out[:300]}")
    return out


def jina_read(url: str, timeout: int = 45) -> str:
    headers = {"Accept": "text/plain"}
    if os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    # Jina answers 403 to browser User-Agents: identify as a tool.
    status, body = http_call(JINA_URL + url, headers=headers, timeout=timeout, ua=TOOL_UA)
    text = body.decode("utf-8", "replace")
    head = text[:4096].casefold()
    if status != 200:
        raise ScrapeError(f"Jina returned HTTP {status}")
    if "target url returned error" in head or "requiring captcha" in head or "title: just a moment" in head:
        raise ScrapeError("Jina received a block page")
    return text


_INTERNAL_SUFFIXES = (
    ".local", ".internal", ".lan", ".test", ".localhost", ".corp", ".home", ".intranet",
    ".localdomain", ".home.arpa", ".ts.net", ".onion",
)
# Parameter names that usually carry credentials. "key", "sig", "code" and "sid" only as whole
# words, so "keywords" or "design" are not flagged.
_SECRET_PARAM = re.compile(
    r"token|secret|passw|pwd|signature|credential|apikey|jwt|sess|auth(?!or)"
    r"|(?:^|[_\-.])(?:key|sig|code|sid)(?:$|[_\-.])",
    re.I,
)


def is_public_host(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        return "." in h and not h.endswith(_INTERNAL_SUFFIXES)
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_global


def has_secret_params(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    return any(
        _SECRET_PARAM.search(name)
        for part in (p.query, p.fragment)
        for name, _ in urllib.parse.parse_qsl(part, keep_blank_values=True)
    )


def remote_ok(url: str, a) -> bool:
    """Remote readers (Exa/Jina) only ever receive public URLs that carry no credentials."""
    if getattr(a, "local_only", False):
        return False
    if any(getattr(a, k, None) for k in ("cookie", "header", "profile", "cdp")) or proxy_of(a):
        return False
    p = urllib.parse.urlsplit(url)
    if p.username or p.password:
        return False
    return is_public_host(p.hostname or "") and not has_secret_params(url)


# ── fetch engine ─────────────────────────────────────────────────────────────


class _DropNoise(logging.Filter):
    # The engine logs "no Cloudflare challenge" (the normal case) and exhausted retries as ERROR;
    # the CLI already reports both on its own line.
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "No Cloudflare challenge found" not in msg and not msg.startswith("Failed after")


def engine() -> None:
    try:
        import scrapling  # noqa: F401
        from scrapling.core.utils import log as slog
    except ImportError:
        raise ScrapeError("Engine not installed. Run once: scrape setup", EXIT_NO_ENGINE)
    slog.setLevel(logging.INFO if STATE["verbose"] else logging.ERROR)
    if not any(isinstance(flt, _DropNoise) for flt in slog.filters):
        slog.addFilter(_DropNoise())


def text_len(page) -> int:
    try:
        return len(page.get_all_text(strip=True, ignore_tags=IGNORED_TAGS))
    except Exception:
        return 0


def content_type(page) -> str:
    for k, v in (page.headers or {}).items():
        if str(k).lower() == "content-type":
            return str(v).split(";")[0].strip().lower()
    return ""


def css(page, selector: str):
    try:
        return page.css(selector)
    except Exception as e:
        raise ScrapeError(f"Invalid CSS selector '{selector}': {e}", EXIT_USAGE)


_PSEUDO = re.compile(r"::(text|attr\([^)]+\))\s*$")

BLOCK_STATUS = {401, 403, 407, 429, 444, 503, 520, 521, 522, 523, 525, 526}
_TITLE_BLOCK = re.compile(
    r"^just a moment|^un momento|attention required|^access denied|robot check|^security check"
    r"|are you (a )?human|pardon our interruption|^captcha$",
    re.I,
)
# Structural (HTML) markers of challenge pages. Only unambiguous ones: Cloudflare, DataDome and
# PerimeterX scripts are also present on ordinary pages of the sites that use them.
_HTML_MARKERS = [
    ("cloudflare", re.compile(r"cf_chl_opt|cf-chl-|challenge-error-text|cf-browser-verification|id=\"challenge-form\"")),
    ("datadome", re.compile(r"captcha-delivery\.com")),
    ("perimeterx", re.compile(r"id=\"px-captcha\"|px-captcha-wrapper")),
    ("imperva", re.compile(r"_incapsula_resource|incapsula incident id")),
    ("akamai", re.compile(r"errors\.edgesuite\.net")),
]
_TEXT_MARKERS = re.compile(
    r"verif(y|ying) (that )?you are (a )?human|are you a robot|unusual traffic|blocked by network security"
    r"|enable javascript and cookies to continue|complete the security check|checking your browser"
    r"|access to this page has been denied|press & hold|enter the characters you see below"
    r"|automated access to amazon|request unsuccessful\. incapsula",
    re.I,
)
_JS_ROOT = re.compile(
    r"""id=["'](root|app|__next|__nuxt|svelte|app-root)["']|ng-version=|data-reactroot|<noscript>[^<]{0,300}javascript""",
    re.I,
)
_LOGIN_PATH = re.compile(
    r"/(login|log-in|signin|sign-in|authwall|checkpoint|accounts/login|i/flow/login|uas/login)(/|$)", re.I
)


def diagnose(page, requested_url: str, a, rendered: bool = False):
    """-> (kind, reason) with kind in blocked|empty|login|notfound, or None when the page is usable.

    `rendered` means the page came from a browser: a short HTML is then no longer an un-rendered shell.
    """
    status = int(page.status or 0)
    if status in (404, 410):
        return ("notfound", f"HTTP {status}")
    if status in BLOCK_STATUS or status >= 400:
        return ("blocked", f"HTTP {status}")
    ct = content_type(page)
    if ct and "html" not in ct:
        return None
    final_path = urllib.parse.urlsplit(str(page.url or "")).path
    if _LOGIN_PATH.search(final_path) and not _LOGIN_PATH.search(urllib.parse.urlsplit(requested_url).path):
        return ("login", "redirects to a login page")
    probe = getattr(a, "each", None) or getattr(a, "selector", None)
    if probe:
        # If the requested selector is there, this is the real page even with leftover anti-bot markers.
        return None if css(page, _PSEUDO.sub("", probe)) else ("empty", f"selector '{probe}' matched nothing")
    if getattr(a, "no_detect", False):
        return None
    try:
        text = str(page.get_all_text(strip=True, ignore_tags=IGNORED_TAGS))
    except Exception:
        text = ""
    tlen = len(text)
    title = clean(page.css("title::text").get() or "")
    if tlen < 4000 and _TITLE_BLOCK.search(title):
        return ("blocked", f"challenge page ('{title[:60]}')")
    if tlen < 2500:
        head = page.body[:300_000].decode("utf-8", "ignore").lower()
        for name, rx in _HTML_MARKERS:
            if rx.search(head):
                return ("blocked", f"anti-bot {name}")
        m = _TEXT_MARKERS.search(text)
        if m:
            return ("blocked", f"challenge page ('{m.group(0)}')")
        if not rendered and tlen < 200 and "<script" in head:
            return ("empty", f"no content without JavaScript ({tlen} characters)")
        if not rendered and tlen < 800 and _JS_ROOT.search(head):
            return ("empty", f"un-rendered SPA ({tlen} characters)")
    return None


def tier_http(url: str, a):
    from scrapling.fetchers import Fetcher

    base = {"timeout": getattr(a, "timeout", 30), "retries": 2, "stealthy_headers": True}
    if getattr(a, "impersonate", None):
        base["impersonate"] = a.impersonate
    if proxy_of(a):
        base["proxy"] = proxy_of(a)
    has_creds = bool(getattr(a, "cookie", None) or getattr(a, "header", None))
    lang = accept_language(a)
    public_start = is_public_host(host_of(url))
    for _ in range(10):
        kw, headers = dict(base), {}
        if has_creds and creds_allowed(url, a):
            if getattr(a, "cookie", None):
                kw["cookies"] = parse_cookies(a.cookie)
            headers = parse_headers(getattr(a, "header", None))
        if lang and not any(k.lower() == "accept-language" for k in headers):
            headers["Accept-Language"] = lang
        if headers:
            kw["headers"] = headers
        if not has_creds:
            return Fetcher.get(url, **kw)
        # With credentials, redirects are followed by hand: libcurl would forward the headers to another domain.
        page = Fetcher.get(url, follow_redirects=False, **kw)
        location = next((str(v) for k, v in (page.headers or {}).items() if str(k).lower() == "location"), None)
        if int(page.status or 0) not in (301, 302, 303, 307, 308) or not location:
            return page
        url = urllib.parse.urljoin(url, location)
        if public_start and not is_public_host(host_of(url)):
            raise ScrapeError(f"Blocked redirect to an internal host: {host_of(url)}")
    raise ScrapeError("Too many redirects")


def fetch_bytes(url: str, a) -> bytes:
    """Plain GET for JSON/XML: the HTTP engine when installed, urllib otherwise."""
    try:
        engine()
    except ScrapeError:
        status, body = http_call(url, timeout=30)
    else:
        try:
            page = tier_http(url, a)
        except ScrapeError:
            raise
        except Exception as e:
            raise ScrapeError(f"Cannot reach {host_of(url)}: {clean(str(e)).split('. See ')[0][:160]}")
        status, body = int(page.status or 0), page.body
    if status >= 400:
        raise ScrapeError(f"HTTP {status} for {url}")
    return body


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def profile_dir(name: str) -> Path:
    # No dots or slashes: ".." would point outside the profiles folder.
    if not _PROFILE_NAME.match(name or ""):
        raise ScrapeError("Invalid profile name: letters, digits, hyphen and underscore only", EXIT_USAGE)
    return PROFILES_DIR / name


def profile_info(name: str) -> dict:
    d = profile_dir(name)
    if not d.is_dir():
        raise ScrapeError(f"Profile '{name}' does not exist. Create it with: scrape login {name} URL", EXIT_USAGE)
    info = {"dir": str(d), "real_chrome": False}
    meta = d / "scrape-profile.json"
    if meta.exists():
        try:
            info.update(json.loads(meta.read_text()))
            info["dir"] = str(d)
        except ValueError:
            pass
    return info


def chrome_installed() -> bool:
    return Path("/Applications/Google Chrome.app").exists() or bool(
        shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    )


class Browsers:
    """Opens the browser once, on demand, and reuses it for the whole batch."""

    def __init__(self, a, first_url: str = ""):
        self.a, self.first_url, self.open = a, first_url, {}

    def session(self, kind: str):
        if kind in self.open:
            return self.open[kind]
        from scrapling.fetchers import DynamicSession, StealthySession

        a = self.a
        kw = {"headless": not getattr(a, "headful", False), "retries": 2, "block_ads": True}
        prof = profile_info(a.profile) if getattr(a, "profile", None) else None
        if getattr(a, "real_chrome", False) or (prof and prof.get("real_chrome")):
            kw["real_chrome"] = True
        if getattr(a, "cdp", None):
            kw["cdp_url"] = a.cdp
        if prof:
            kw["user_data_dir"] = prof["dir"]
        if proxy_of(a):
            kw["proxy"] = proxy_of(a)
        pattern = getattr(a, "xhr", None) or (".*" if getattr(a, "xhr_list", False) else None)
        if pattern:
            kw["capture_xhr"] = pattern
        if getattr(a, "locale", None):
            kw["locale"] = a.locale
        elif is_reddit(self.first_url):
            kw["locale"] = "en-US"  # in any other language Reddit serves a machine-translated thread
        cookies = []
        if prof:
            jar = Path(prof["dir"]) / "session-cookies.json"
            if jar.exists():
                try:
                    cookies += json.loads(jar.read_text())
                except ValueError:
                    pass
        if getattr(a, "cookie", None) and self.first_url:
            host = host_of(self.first_url)
            cookies += [{"name": k, "value": v, "domain": host, "path": "/"} for k, v in parse_cookies(a.cookie).items()]
        if cookies:
            kw["cookies"] = cookies
        cls = StealthySession if kind == "stealth" else DynamicSession
        try:
            s = cls(**kw)
            s.start()
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                raise
            # Browser launches fail transiently once in a while; a second attempt almost always works.
            log(f"{kind}: browser launch failed ({clean(str(e))[:80]}); retrying once")
            s = cls(**kw)
            s.start()
        self.open[kind] = s
        return s

    def fetch(self, kind: str, url: str):
        a = self.a
        kw = {
            "timeout": max(getattr(a, "timeout", 30), 60) * 1000,
            "network_idle": not getattr(a, "no_network_idle", False),
        }
        if getattr(a, "wait", 0):
            kw["wait"] = a.wait
        if getattr(a, "wait_selector", None):
            kw["wait_selector"] = a.wait_selector
        extra = {k.lower(): v for k, v in parse_headers(getattr(a, "header", None)).items()}
        site = getattr(a, "auth_site", None)
        if extra and site:
            # extra_headers would send them to everything the page loads (CDN, analytics, iframes).
            # Playwright limit: if the site itself redirects to another domain, the headers follow the redirect.
            if any(re.search(r"auth|token|key|secret|cookie", k) for k in extra):
                log("warning: in the browser, -H headers follow redirects issued by the site; for tokens -m http is safer")

            def setup(page):
                def add_headers(route, request):
                    if host_matches(request.url, site):
                        route.fallback(headers={**request.headers, **extra})
                    else:
                        route.fallback()

                page.route("**/*", add_headers)

            kw["page_setup"] = setup
        if getattr(a, "fast", False):
            kw["disable_resources"] = True
        if kind == "stealth":
            kw["solve_cloudflare"] = True
        shot, scroll = getattr(a, "screenshot", None), getattr(a, "scroll", 0)
        if shot or scroll:

            def act(page):
                for _ in range(scroll or 0):
                    page.mouse.wheel(0, 25000)
                    page.wait_for_timeout(900)
                if shot:
                    Path(shot).expanduser().parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(shot).expanduser()), full_page=True)

            kw["page_action"] = act
        return self.session(kind).fetch(url, **kw)

    def close(self) -> None:
        for s in self.open.values():
            try:
                s.close()
            except Exception:
                pass
        self.open = {}


class Result:
    def __init__(self, url, tier, page=None, remote_md=None, problem=None, tried=None):
        self.url, self.tier, self.page, self.remote_md = url, tier, page, remote_md
        self.problem, self.tried = problem, tried or []


def wants_browser(a) -> bool:
    return any(
        getattr(a, k, None)
        for k in ("xhr", "xhr_list", "screenshot", "profile", "cdp", "headful", "real_chrome", "wait_selector", "wait", "scroll")
    )


def needs_dom(a) -> bool:
    """Options that need the real HTML (remote readers only return Markdown)."""
    return bool(
        any(getattr(a, k, None) for k in ("selector", "each", "field", "links", "meta", "data", "next", "raw", "outline"))
        or getattr(a, "format", "md") == "html"
    )


def fetch_one(url: str, a, browsers: Browsers, prefetched=None) -> Result:
    """Escalation: http -> stealth (solves Cloudflare) -> remote readers Exa/Jina."""
    mode = getattr(a, "mode", "auto")
    if mode == "auto":
        tiers = ["stealth"] if wants_browser(a) or is_reddit(url) else ["http", "stealth"]
        if not wants_browser(a) and not needs_dom(a) and remote_ok(url, a):
            tiers += ["exa"] if is_reddit(url) else ["exa", "jina"]
    else:
        tiers = [mode]
    if set(tiers) & {"http", "stealth", "dynamic"}:
        engine()
    tried, best, statuses = [], None, []
    for tier in tiers:
        t0 = time.time()
        try:
            if tier == "http":
                page = prefetched if prefetched is not None else tier_http(url, a)
                if isinstance(page, Exception):
                    raise page
            elif tier in ("stealth", "dynamic"):
                page = browsers.fetch(tier, url)
            else:
                # The URL only goes to a third party if this machine reached the site and was not asked to authenticate.
                if mode == "auto" and (not statuses or any(code in (401, 407) for code in statuses)):
                    why = "the site asks for authentication" if statuses else "no local tier got a response"
                    log(f"{tier}: skipped ({why}); force it with -m {tier}")
                    continue
                md = jina_read(url) if tier == "jina" else exa_call(
                    "web_fetch_exa", {"urls": [url], "maxCharacters": 200_000}
                )
                if len(md.strip()) < 200:
                    raise ScrapeError("empty response")
                log(f"{tier}: ok · {len(md):,} characters · {time.time() - t0:.1f}s (remote reader: the URL is sent to {tier})")
                return Result(url, tier, remote_md=md, tried=tried)
        except ScrapeError as e:
            if e.code in (EXIT_NO_ENGINE, EXIT_USAGE):
                raise
            tried.append(f"{tier}: {e}")
            log(f"{tier}: {e}")
            continue
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                raise ScrapeError("The Chromium browser is missing. Run: scrape setup --upgrade", EXIT_NO_ENGINE)
            msg = clean(str(e)).split(". See ")[0][:160] or type(e).__name__
            tried.append(f"{tier}: {msg}")
            log(f"{tier}: error · {msg}")
            continue
        finally:
            prefetched = None
        statuses.append(int(page.status or 0))
        problem = diagnose(page, url, a, rendered=tier != "http")
        mark = "ok" if not problem else f"rejected: {problem[1]}"
        log(f"{tier}: HTTP {page.status} · {len(page.body):,} bytes · {time.time() - t0:.1f}s · {mark}")
        res = Result(url, tier, page=page, problem=problem, tried=tried)
        if not problem:
            return res
        tried.append(f"{tier}: {problem[1]}")
        best = res
        if problem[0] in ("notfound", "login"):
            break
    if best:
        best.tried = tried
        return best
    return Result(url, tiers[-1], problem=("error", "no tier responded"), tried=tried)


def failure_text(res: Result) -> tuple[str, int]:
    kind = res.problem[0] if res.problem else "error"
    lines = [f"Could not fetch {res.url}"] + [f"  - {t}" for t in res.tried]
    if kind == "error":
        lines.append(
            "No tier got a response: mistyped URL, no connection, timeout (--timeout) or the site dropped the "
            "connection. If the URL is public, -m exa or -m jina read it from another network."
        )
        return "\n".join(lines), EXIT_ERROR
    if kind == "notfound":
        lines.append("The page does not exist (404/410): check the URL.")
        return "\n".join(lines), EXIT_ERROR
    if kind == "login":
        lines.append(
            "A logged-in session is required. Options: `scrape login NAME URL` and retry with `--profile NAME`, "
            "or use Claude in Chrome (the user's real session). See references/login-sessions.md"
        )
        return "\n".join(lines), EXIT_BLOCKED
    if kind == "empty":
        lines.append("Try without the selector, with --wait-selector CSS or --scroll N, or inspect the DOM with --outline.")
        return "\n".join(lines), EXIT_ERROR
    lines.append(
        "Next steps: --real-chrome, --headful, --proxy http://user:pass@host:port (residential), "
        "or a logged-in session (--profile). If you believe the page was fine, retry with --no-detect. "
        "See references/troubleshooting.md"
    )
    return "\n".join(lines), EXIT_BLOCKED


# ── extraction and rendering ─────────────────────────────────────────────────────

FOCUS_CANDIDATES = (
    "main",
    "[role=main]",
    "article",
    "#main-content",
    "#main",
    "#content",
    ".main-content",
    ".post-content",
    ".entry-content",
    ".article-body",
)
_DATA_URI = re.compile(r"\(data:[^)\s]{40,}\)")


def pick_focus(page) -> str | None:
    """The single main container that keeps at least half of the body text."""
    total = text_len(page)
    if total < 400:
        return None
    for sel in FOCUS_CANDIDATES:
        try:
            els = page.css(sel)
        except Exception:
            continue
        if len(els) != 1:
            continue
        if len(els[0].get_all_text(strip=True, ignore_tags=IGNORED_TAGS)) >= 0.5 * total:
            return sel
    return None


_MD_HREF = re.compile(r"\]\((?![a-zA-Z][a-zA-Z0-9+.-]*:|#)([^)\s]+)")


def tidy_md(md: str, compact: bool = False, base: str = "") -> str:
    md = _DATA_URI.sub("(data:…)", md)
    if compact:
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
        md = re.sub(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)", r"\1", md)
    elif base:
        md = _MD_HREF.sub(lambda m: "](" + urllib.parse.urljoin(base, m.group(1)), md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def jsonld(page) -> list:
    out = []
    for el in page.css('script[type="application/ld+json"]'):
        try:
            out.append(json.loads(str(el.text)))
        except ValueError:
            continue
    return out


def extract_meta(page) -> dict:
    def one(sel):
        v = page.css(sel).get()
        return clean(v) if v else None

    social = {}
    for el in page.css('meta[property^="og:"], meta[name^="twitter:"], meta[property^="article:"]'):
        key = el.attrib.get("property") or el.attrib.get("name")
        if key:
            social[key] = el.attrib.get("content")
    base = str(page.url)
    feeds = [
        urllib.parse.urljoin(base, str(h))
        for kind in ("rss", "atom")
        for h in page.css(f'link[rel="alternate"][type*="{kind}"]::attr(href)').getall()
    ]
    return {
        "url": base,
        "status": page.status,
        "title": one("title::text"),
        "description": one('meta[name="description"]::attr(content)'),
        "canonical": one('link[rel="canonical"]::attr(href)'),
        "lang": one("html::attr(lang)"),
        "h1": [clean(e.get_all_text(strip=True)) for e in page.css("h1")][:5],
        "social": social,
        "feeds": feeds,
        "jsonld": jsonld(page),
    }


_STATE_ASSIGN = re.compile(r"(?:window\.)?(__[A-Z0-9_]{4,}__)\s*=\s*(?=[{\[])")


def extract_data(page) -> dict:
    """Embedded JSON: JSON-LD, __NEXT_DATA__ and friends, and state blobs like window.__X__ = {...}."""
    out = {"jsonld": jsonld(page), "json_scripts": {}, "state": {}}
    for i, el in enumerate(page.css('script[type="application/json"], script#__NEXT_DATA__, script#__NUXT_DATA__')):
        key = el.attrib.get("id") or el.attrib.get("data-target") or f"json_{i}"
        try:
            out["json_scripts"][key] = json.loads(str(el.text))
        except ValueError:
            continue
    dec = json.JSONDecoder()
    for el in page.css("script:not([src])"):
        src = str(el.text or "")
        for m in _STATE_ASSIGN.finditer(src):
            try:
                out["state"][m.group(1)], _ = dec.raw_decode(src, m.end())
            except ValueError:
                continue
    return {k: v for k, v in out.items() if v}


def render_links(page, a) -> tuple[str, str]:
    base = str(page.url)
    site = host_of(base)
    site = site[4:] if site.startswith("www.") else site
    rx = compile_rx(getattr(a, "match", None), "--match")
    seen, rows = set(), []
    for root in css(page, a.selector) if a.selector else [page]:
        for el in root.css("a[href]"):
            href = urllib.parse.urljoin(base, str(el.attrib.get("href", "")).strip()).split("#")[0]
            if not href.startswith(("http://", "https://")) or href in seen:
                continue
            if getattr(a, "same_domain", False) and not host_matches(href, site):
                continue
            if rx and not rx.search(href):
                continue
            seen.add(href)
            rows.append({"text": clean(el.get_all_text(strip=True))[:120], "url": href})
    if getattr(a, "limit", 0):
        rows = rows[: a.limit]
    if a.format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2), "json"
    body = "\n".join(f"- [{r['text']}]({r['url']})" if r["text"] else f"- {r['url']}" for r in rows)
    return f"{body}\n\n{len(rows)} links\n", "md"


def field_values(root, selector: str, base: str) -> list:
    if _PSEUDO.search(selector):
        vals = [clean(v) for v in root.css(selector).getall()]
        if re.search(r"::attr\((href|src)\)\s*$", selector):
            vals = [urllib.parse.urljoin(base, v) for v in vals if v]
    else:
        vals = [clean(e.get_all_text(strip=True, ignore_tags=IGNORED_TAGS)) for e in root.css(selector)]
    return [v for v in vals if v]


def extract_rows(page, a) -> list:
    """--each row + --field name=selector -> list of objects. `name[]` returns every match."""
    base = str(page.url)
    specs = []
    for f in a.field:
        if "=" not in f:
            raise ScrapeError(f"--field must be name=selector: {f}", EXIT_USAGE)
        name, sel = f.split("=", 1)
        specs.append((name.strip(), sel.strip()))
    rows = []
    for root in css(page, a.each) if a.each else [page]:
        row = {}
        for name, sel in specs:
            try:
                vals = field_values(root, sel, base)
            except Exception as e:
                raise ScrapeError(f"Invalid selector in --field {name}: {e}", EXIT_USAGE)
            if name.endswith("[]"):
                row[name[:-2]] = vals
            else:
                row[name] = vals[0] if vals else None
        # With --each, rows with no field at all (headers, separators) add nothing.
        if not a.each or any(row.values()):
            rows.append(row)
    limit = getattr(a, "limit", 0)
    return rows[:limit] if limit else rows


def render_outline(page, a) -> str:
    """DOM skeleton (tag#id.classes, repeats and text length) for choosing selectors."""
    roots = css(page, a.selector) if a.selector else (page.css("body") or [page])
    lines = []

    def sig(el) -> str:
        ident = str(el.tag)
        if el.attrib.get("id"):
            ident += "#" + str(el.attrib["id"])
        classes = str(el.attrib.get("class") or "").split()
        return ident + "".join("." + c for c in classes[:3])

    def walk(el, depth: int) -> None:
        if depth > a.outline or len(lines) >= 400:
            return
        groups = {}
        for ch in el.children:
            if ch.tag not in IGNORED_TAGS + ("link", "meta", "br", "hr"):
                groups.setdefault(sig(ch), []).append(ch)
        for name, els in groups.items():
            first = els[0]
            text = clean(first.get_all_text(strip=True, ignore_tags=IGNORED_TAGS))
            line = "  " * depth + name + (f" ×{len(els)}" if len(els) > 1 else "") + f" ({len(text)})"
            if not first.children and text:
                line += f" “{text[:50]}”"
            lines.append(line)
            walk(first, depth + 1)

    for root in roots[:1]:
        lines.append(sig(root))
        walk(root, 1)
    return "\n".join(lines) + "\n"


def render_xhr(page, a) -> tuple[str, str]:
    items = []
    for x in page.captured_xhr:
        body = x.body or b""
        ct = content_type(x)
        entry = {"url": str(x.url), "status": x.status, "type": ct, "bytes": len(body)}
        if not a.xhr_list:
            try:
                entry["json"] = json.loads(body)
            except ValueError:
                entry["text"] = body[:5000].decode("utf-8", "replace")
        items.append(entry)
    return json.dumps(items, ensure_ascii=False, indent=2), "json"


def render_feed(raw: bytes, a, source: str) -> str:
    try:
        import feedparser
    except ImportError:
        raise ScrapeError("feedparser is not installed (scrape setup)", EXIT_NO_ENGINE)
    d = feedparser.parse(raw)
    if not d.entries:
        raise ScrapeError("Not a valid RSS/Atom feed, or it is empty")
    n = getattr(a, "n", None) or 15
    full = getattr(a, "full", False)
    lines = [f"# {d.feed.get('title') or source}", f"Source: {source} · {len(d.entries)} entries (showing {min(n, len(d.entries))})", ""]
    for e in d.entries[:n]:
        date = e.get("published") or e.get("updated") or ""
        body = e.get("content", [{}])[0].get("value") if e.get("content") else e.get("summary", "")
        text = strip_html(body or "")
        lines.append(f"- [{clean(e.get('title') or '(untitled)')}]({e.get('link', '')})" + (f" — {date}" if date else ""))
        if text:
            lines.append(f"  {text if full else text[:300]}")
    return "\n".join(lines) + "\n"


def is_reddit(url: str) -> bool:
    return host_matches(url, "reddit.com", "redd.it")


def render_reddit(page, a):
    """A Reddit thread built from its <shreddit-post>/<shreddit-comment> components; None if this is not a thread."""
    posts, comments = page.css("shreddit-post"), page.css("shreddit-comment")
    if not posts:
        return None
    post = posts[0].attrib
    rows = []
    for c in comments:
        at = c.attrib
        own = c.css(f'[id="{at.get("thingid", "")}-comment-rtjson-content"]')
        text = clean(own[0].get_all_text(strip=True, ignore_tags=IGNORED_TAGS)) if own else ""
        if text:
            rows.append(
                {
                    "author": at.get("author"),
                    "depth": int(at.get("depth") or 0),
                    "score": at.get("score"),
                    "created": str(at.get("created") or "")[:10],
                    "text": text,
                    "url": urllib.parse.urljoin("https://www.reddit.com", str(at.get("permalink") or "")),
                }
            )
    # The post text lives in #<id>-post-rtjson-content; [slot=text-body] wraps it twice, next to the "Read more" button.
    body = page.markdown(css_selector='shreddit-post [id$="-post-rtjson-content"]', main_content_only=True).strip()
    if getattr(a, "format", "md") == "json":
        doc = {
            "title": post.get("post-title"),
            "subreddit": post.get("subreddit-prefixed-name"),
            "author": post.get("author"),
            "created": post.get("created-timestamp"),
            "score": post.get("score"),
            "comment_count": post.get("comment-count"),
            "url": str(page.url),
            "body": body,
            "comments": rows,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2), "json"
    out = [
        f"# {post.get('post-title', '(untitled)')}",
        f"{post.get('subreddit-prefixed-name', '')} · u/{post.get('author', '?')} · {str(post.get('created-timestamp') or '')[:10]}"
        f" · {post.get('score', '?')} points · {post.get('comment-count', '?')} comments",
        f"URL: {page.url}",
    ]
    if post.get("post-type") == "link" and post.get("content-href"):
        out.append(f"Link: {post.get('content-href')}")
    if body:
        out += ["", tidy_md(body, getattr(a, "compact", False), str(page.url)).strip()]
    out += ["", f"## Comments ({len(rows)} loaded of {post.get('comment-count', '?')})", ""]
    out += [f"{'  ' * r['depth']}- **u/{r['author']}** ({r['score']} pts): {r['text']}" for r in rows]
    return "\n".join(out) + "\n", "md"


def render_page(page, a) -> tuple[str, str]:
    """-> (content, extension) depending on the response type and the options requested."""
    if getattr(a, "xhr", None) or getattr(a, "xhr_list", False):
        return render_xhr(page, a)
    ct, raw = content_type(page), page.body
    is_html = "html" in ct or (not ct and b"<html" in raw[:2000].lower())
    if not is_html:
        if "json" in ct or raw[:1] in (b"{", b"["):
            try:
                return json.dumps(json.loads(raw), ensure_ascii=False, indent=2), "json"
            except ValueError:
                pass
        if "xml" in ct or raw.lstrip()[:5] == b"<?xml":
            if re.search(rb"<(rss|feed|rdf:RDF)[\s>]", raw[:3000]):
                return render_feed(raw, a, str(page.url)), "md"
            return raw.decode(page.encoding or "utf-8", "replace"), "xml"
        if ct.startswith("text/"):
            return raw.decode(page.encoding or "utf-8", "replace"), "txt"
        if ct:
            raise BinaryContent(raw, ct)
    fmt = getattr(a, "format", "md")
    if getattr(a, "outline", None):
        return render_outline(page, a), "txt"
    if getattr(a, "links", False):
        return render_links(page, a)
    if getattr(a, "meta", False):
        return json.dumps(extract_meta(page), ensure_ascii=False, indent=2), "json"
    if getattr(a, "data", False):
        return json.dumps(extract_data(page), ensure_ascii=False, indent=2), "json"
    if getattr(a, "field", None):
        rows = extract_rows(page, a)
        return (rows_to_csv(rows), "csv") if getattr(a, "csv", False) else (json.dumps(rows, ensure_ascii=False, indent=2), "json")
    sel = getattr(a, "selector", None)
    if not sel and fmt in ("md", "json") and not getattr(a, "raw", False) and is_reddit(str(page.url)):
        thread = render_reddit(page, a)
        if thread:
            return thread
    if sel and _PSEUDO.search(sel):
        vals = field_values(page, sel, str(page.url))
        return (json.dumps(vals, ensure_ascii=False, indent=2), "json") if fmt == "json" else ("\n".join(vals) + "\n", "txt")
    if fmt == "html":
        if sel:
            return "\n".join(str(e.html_content) for e in css(page, sel)), "html"
        return raw.decode(page.encoding or "utf-8", "replace"), "html"
    raw_mode = getattr(a, "raw", False)
    focus = None if (sel or raw_mode or getattr(a, "full_page", False)) else pick_focus(page)
    if focus:
        log(f"focus: <{focus}> (use --full-page for the whole body)")
    target = sel or focus
    title = clean(page.css("title::text").get() or "")
    if fmt == "text":
        from scrapling.core.shell import Convertor

        body = "".join(Convertor._extract_content(page, "text", css_selector=target, main_content_only=not raw_mode)).strip()
    else:
        body = tidy_md(page.markdown(css_selector=target, main_content_only=not raw_mode), getattr(a, "compact", False), str(page.url))
    if not body.strip():
        raise ScrapeError(
            "The page loaded but no visible text is left after cleaning (hidden content, or behind a login). "
            "Try --meta, --data, --raw or a logged-in session (--profile).",
            EXIT_BLOCKED,
        )
    if fmt == "json":
        doc = {"url": str(page.url), "status": page.status, "title": title, "markdown": body}
        return json.dumps(doc, ensure_ascii=False, indent=2), "json"
    header = "" if (sel or getattr(a, "no_header", False)) else f"Title: {title}\nURL: {page.url}\n\n---\n\n"
    return header + body.strip() + "\n", "txt" if fmt == "text" else "md"


class BinaryContent(Exception):
    def __init__(self, data: bytes, ctype: str):
        super().__init__(ctype)
        self.data, self.ctype = data, ctype


def render_result(res: Result, a) -> tuple[str, str]:
    if res.remote_md is not None:
        return tidy_md(res.remote_md, getattr(a, "compact", False)), "md"
    return render_page(res.page, a)


# ── platform routes─────────────────────────────────────────────────────────────


def ytdlp_base() -> list:
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    if not exe:
        raise ScrapeError("yt-dlp is not installed. Run: scrape setup", EXIT_NO_ENGINE)
    return [exe]


def ytdlp_cmd() -> list:
    cmd = ytdlp_base() + ["--no-warnings", "--no-playlist"]
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    return cmd


def ytdlp_json(args: list, timeout: int = 180) -> dict:
    p = run(ytdlp_cmd() + args, timeout)
    if p.returncode != 0 or not p.stdout.strip():
        err = [ln for ln in p.stderr.strip().splitlines() if ln.strip()]
        raise ScrapeError("yt-dlp: " + (err[-1][:300] if err else "no output"))
    return json.loads(p.stdout)


def parse_json3(path: Path) -> list:
    out = []
    for ev in json.loads(path.read_text(encoding="utf-8")).get("events", []):
        text = clean("".join(s.get("utf8", "") for s in ev.get("segs") or []))
        if text:
            out.append((ev.get("tStartMs", 0) / 1000.0, text))
    return out


def parse_vtt(path: Path) -> list:
    out, last, t = [], "", 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})\.\d{3}\s+-->", line)
        if m:
            t = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            continue
        line = htmllib.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        if not line or line == last or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        out.append((float(t), line))
        last = line
    return out


def pick_sub(info: dict, prefs: list, explicit: bool):
    """-> (key, is_automatic).

    Without --lang the original track wins (manual, else automatic): it is the most faithful.
    With --lang that language wins, even when it is an automatic translation.
    """
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}

    def match(pool, lang):
        if lang in pool:
            return lang
        return next((k for k in pool if k.split("-")[0] == lang), None)

    orig = (info.get("language") or "").split("-")[0]
    manual_prefs = [(subs, p, False) for p in prefs]
    auto_prefs = [(auto, p, True) for p in prefs]
    original = [(subs, orig, False)] if orig else []
    original_auto = [(auto, f"{orig}-orig", True), (auto, orig, True)] if orig else []
    if explicit:
        steps = manual_prefs + auto_prefs + original + original_auto
    else:
        steps = original + manual_prefs + original_auto + auto_prefs
    for pool, lang, is_auto in steps:
        k = match(pool, lang)
        if k:
            return k, is_auto
    if subs:
        return next(iter(subs)), False
    return None, False


def fetch_transcript(url: str, key: str, is_auto: bool) -> list:
    with tempfile.TemporaryDirectory(prefix="scrape-yt-") as tmp:
        flag = "--write-auto-subs" if is_auto else "--write-subs"
        run(
            ytdlp_cmd() + ["--skip-download", flag, "--sub-langs", key, "--sub-format", "json3/vtt/best", "-o", f"{tmp}/s.%(ext)s", url],
            180,
        )
        for f in sorted(Path(tmp).glob("s.*")):
            if f.suffix == ".json3":
                return parse_json3(f)
            if f.suffix == ".vtt":
                return parse_vtt(f)
    return []


def transcript_md(lines: list, chapters: list, timestamps: bool) -> str:
    out, buf, start, size = [], [], None, 0
    pending = sorted(chapters or [], key=lambda c: c.get("start_time", 0))

    def flush():
        nonlocal buf, start, size
        if buf:
            prefix = f"[{fmt_ts(start)}] " if timestamps else ""
            out.append(prefix + " ".join(buf))
        buf, start, size = [], None, 0

    for t, text in lines:
        while pending and t >= pending[0].get("start_time", 0):
            flush()
            ch = pending.pop(0)
            out.append(f"### {ch.get('title', 'Chapter')} ({fmt_ts(ch.get('start_time', 0))})")
        if start is None:
            start = t
        buf.append(text)
        size += len(text) + 1
        if (size >= 500 and re.search(r"[.!?…]$", text)) or size >= 900:
            flush()
    flush()
    return "\n\n".join(out)


def handle_video(url: str, a) -> tuple[str, str]:
    n_comments = getattr(a, "comments", 0) or 0
    args = ["--dump-single-json", "--skip-download", "--flat-playlist"]
    if n_comments:
        args += ["--write-comments", "--extractor-args", f"youtube:max_comments={n_comments},{n_comments},0,0;comment_sort=top"]
    info = ytdlp_json(args + [url])
    as_json = getattr(a, "format", "md") == "json" or getattr(a, "json", False)
    if info.get("_type") == "playlist":
        entries = (info.get("entries") or [])[: getattr(a, "n", None) or 30]
        rows = [
            {"title": e.get("title"), "url": e.get("url") or e.get("webpage_url"), "duration": e.get("duration"), "channel": e.get("channel") or e.get("uploader")}
            for e in entries
        ]
        if as_json:
            return json.dumps({"playlist": info.get("title"), "entries": rows}, ensure_ascii=False, indent=2), "json"
        body = "\n".join(f"{i}. [{r['title']}]({r['url']})" + (f" · {fmt_ts(r['duration'])}" if r["duration"] else "") for i, r in enumerate(rows, 1))
        return f"# {info.get('title', 'Playlist')}\n{len(info.get('entries') or [])} videos\n\n{body}\n", "md"

    meta_only = getattr(a, "meta_only", False)
    lines, sub_label = [], "skipped (--meta-only)" if meta_only else "not available"
    if not meta_only:
        explicit = bool(getattr(a, "lang", None))
        # Only used when the video does not declare its own language.
        fallback = f"{(system_locale() or 'en').split('-')[0]},en"
        prefs = [x.strip() for x in (getattr(a, "lang", None) or fallback).split(",") if x.strip()]
        key, is_auto = pick_sub(info, prefs, explicit)
        if key:
            lines = fetch_transcript(info.get("webpage_url") or url, key, is_auto)
            sub_label = f"{key} ({'auto-generated' if is_auto else 'manual'})" if lines else f"{key} (download failed)"
    date = info.get("upload_date") or ""
    date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
    comments = [
        {"author": c.get("author"), "likes": c.get("like_count"), "text": c.get("text")}
        for c in (info.get("comments") or [])[:n_comments]
    ]
    if as_json:
        doc = {
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "url": info.get("webpage_url") or url,
            "date": date,
            "duration": info.get("duration"),
            "views": info.get("view_count"),
            "likes": info.get("like_count"),
            "description": info.get("description"),
            "chapters": info.get("chapters"),
            "subtitles": sub_label,
            "transcript": [{"t": round(t, 1), "text": x} for t, x in lines],
            "comments": comments,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2), "json"
    facts = [f"**Channel:** {info.get('channel') or info.get('uploader') or '?'}"]
    if date:
        facts.append(f"**Date:** {date}")
    if info.get("duration"):
        facts.append(f"**Duration:** {fmt_ts(info['duration'])}")
    if info.get("view_count") is not None:
        facts.append(f"**Views:** {info['view_count']:,}")
    if info.get("like_count") is not None:
        facts.append(f"**Likes:** {info['like_count']:,}")
    desc = (info.get("description") or "").strip()
    if not getattr(a, "full", False) and len(desc) > 1500:
        desc = desc[:1500].rstrip() + " […]"
    out = [f"# {info.get('title', '(untitled)')}", " · ".join(facts), f"**URL:** {info.get('webpage_url') or url}", f"**Subtitles:** {sub_label}"]
    if desc:
        out += ["", "## Description", desc]
    if lines:
        out += ["", "## Transcript", transcript_md(lines, info.get("chapters"), not getattr(a, "no_timestamps", False))]
    elif not meta_only:
        out += ["", "_No subtitles: to transcribe the audio instead, see references/platforms.md (YouTube and video)._"]
    if comments:
        out += ["", "## Top comments"] + [f"- **{c['author']}** ({c['likes'] or 0} likes): {clean(c['text'] or '')}" for c in comments]
    return "\n".join(out) + "\n", "md"


def _tweet_token(tid: str) -> str:
    x = (int(tid) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, frac, s = int(x), x - int(x), ""
    while n:
        s = digits[n % 36] + s
        n //= 36
    for _ in range(9):
        frac *= 36
        s += digits[int(frac)]
        frac -= int(frac)
    return s.replace("0", "") or "a"


def _tweet_text(t: dict) -> str:
    text = t.get("text") or ""
    ents = t.get("entities") or {}
    for u in ents.get("urls") or []:
        if u.get("url"):
            text = text.replace(u["url"], u.get("expanded_url") or u["url"])
    for m in ents.get("media") or []:
        if m.get("url"):
            text = text.replace(m["url"], "")
    return htmllib.unescape(text).strip()


def render_tweet(t: dict, quoted: bool = False) -> str:
    u = t.get("user") or {}
    out = [f"**{u.get('name', '?')}** (@{u.get('screen_name', '?')}) · {t.get('created_at', '')}"]
    if t.get("in_reply_to_screen_name"):
        out.append(f"In reply to @{t['in_reply_to_screen_name']}")
    out += ["", _tweet_text(t), ""]
    stats = [f"{label}: {t[k]:,}" for k, label in (("favorite_count", "Likes"), ("conversation_count", "Replies")) if isinstance(t.get(k), int)]
    if stats:
        out.append(" · ".join(stats))
    media = []
    for md in t.get("mediaDetails") or []:
        variants = [v for v in (md.get("video_info") or {}).get("variants", []) if v.get("content_type") == "video/mp4"]
        best = max(variants, key=lambda v: v.get("bitrate", 0)) if variants else None
        media.append(best["url"] if best else md.get("media_url_https"))
    if media:
        out += ["Media:"] + [f"- {m}" for m in media if m]
    if t.get("quoted_tweet"):
        out += ["", "Quoted tweet:"] + ["> " + ln for ln in render_tweet(t["quoted_tweet"], True).splitlines()]
    if not quoted:
        out += ["", f"URL: https://x.com/{u.get('screen_name', 'i')}/status/{t.get('id_str', '')}"]
    return "\n".join(out)


def handle_tweet(url: str, a) -> tuple[str, str]:
    m = re.search(r"/status(?:es)?/(\d+)", url) or re.fullmatch(r"\s*(\d{5,})\s*", url)
    if not m:
        raise ScrapeError("Cannot find the tweet ID in the URL", EXIT_USAGE)
    tid = m.group(1)
    q = urllib.parse.urlencode({"id": tid, "token": _tweet_token(tid), "lang": "en"})
    status, body = http_call(f"https://cdn.syndication.twimg.com/tweet-result?{q}", timeout=20)
    if status != 200 or body.strip() in (b"", b"{}"):
        raise ScrapeError(f"Tweet not available without login (HTTP {status}): deleted, protected or restricted")
    t = json.loads(body)
    if t.get("__typename") != "Tweet":
        raise ScrapeError(f"Tweet not available: {clean((t.get('tombstone') or {}).get('text', {}).get('text', t.get('__typename', '')))}")
    if getattr(a, "format", "md") == "json" or getattr(a, "json", False):
        return json.dumps(t, ensure_ascii=False, indent=2), "json"
    return render_tweet(t) + "\n", "md"


def handle_reddit(url: str, a) -> tuple[str, str]:
    """A subreddit listing through its anonymous RSS feed (it tolerates very few consecutive requests)."""
    p = urllib.parse.urlsplit(url)
    rss = urllib.parse.urlunsplit((p.scheme, "www.reddit.com", p.path.rstrip("/") + "/.rss", p.query, ""))
    status, body = http_call(rss, timeout=20)
    if status == 200 and body.lstrip().startswith(b"<?xml"):
        return render_feed(body, a, url), "md"
    raise ScrapeError(f"Reddit returned HTTP {status} for the listing RSS (anonymous rate limit)")


def _gh(path: str, raw: bool = False) -> bytes:
    accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    if shutil.which("gh"):
        p = subprocess.run(["gh", "api", path, "-H", f"Accept: {accept}"], capture_output=True, timeout=40)
        if p.returncode == 0:
            return p.stdout
    status, body = http_call(f"https://api.github.com/{path}", headers={"Accept": accept}, timeout=30, ua=TOOL_UA)
    if status != 200:
        raise ScrapeError(f"GitHub API HTTP {status} for {path}")
    return body


def handle_github(url: str, a) -> tuple[str, str]:
    parts = [x for x in urllib.parse.urlsplit(url).path.split("/") if x]
    owner, repo = parts[0], parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    meta = json.loads(_gh(f"repos/{owner}/{repo}"))
    try:
        readme = _gh(f"repos/{owner}/{repo}/readme", raw=True).decode("utf-8", "replace")
    except ScrapeError:
        readme = "(no README)"
    if getattr(a, "format", "md") == "json":
        return json.dumps({"repo": meta, "readme": readme}, ensure_ascii=False, indent=2), "json"
    lic = (meta.get("license") or {}).get("spdx_id") or "—"
    out = [
        f"# {meta.get('full_name')}",
        meta.get("description") or "",
        "",
        f"- Stars: {meta.get('stargazers_count', 0):,} · Forks: {meta.get('forks_count', 0):,} · Open issues: {meta.get('open_issues_count', 0):,}",
        f"- Language: {meta.get('language') or '—'} · License: {lic} · Default branch: {meta.get('default_branch')}",
        f"- Created: {(meta.get('created_at') or '')[:10]} · Last push: {(meta.get('pushed_at') or '')[:10]}",
    ]
    if meta.get("topics"):
        out.append(f"- Topics: {', '.join(meta['topics'])}")
    if meta.get("homepage"):
        out.append(f"- Homepage: {meta['homepage']}")
    out += [f"- URL: {meta.get('html_url')}", "", "## README", "", tidy_md(readme, getattr(a, "compact", False))]
    return "\n".join(out), "md"


def route_for(url: str) -> str | None:
    p = urllib.parse.urlsplit(url)
    if host_matches(url, "youtu.be") or (host_matches(url, "youtube.com") and re.match(r"/(watch|shorts/|live/|embed/|playlist)", p.path)):
        return "video"
    if host_matches(url, "x.com", "twitter.com") and re.search(r"/status(es)?/\d+", p.path):
        return "tweet"
    if host_matches(url, "reddit.com") and "/comments/" not in p.path and not re.search(r"/s/\w+", p.path):
        return "reddit"
    if host_matches(url, "github.com") and re.fullmatch(r"/[^/]+/[^/]+/?", p.path) and p.path.split("/")[1] not in ("orgs", "topics", "search", "settings", "marketplace", "sponsors"):
        return "github"
    return None


ROUTES = {"video": handle_video, "tweet": handle_tweet, "reddit": handle_reddit, "github": handle_github}


def routed(url: str, a):
    """Native platform route, when one exists and nothing engine-specific was requested."""
    if getattr(a, "no_route", False) or getattr(a, "mode", "auto") != "auto" or wants_browser(a) or needs_dom(a):
        return None
    name = route_for(url)
    if not name:
        return None
    try:
        out = ROUTES[name](url, a)
        log(f"route: {name}")
        return out
    except ScrapeError as e:
        if e.code in (EXIT_NO_ENGINE, EXIT_USAGE):
            raise
        log(f"route {name} failed ({e}); falling back to the engine")
        return None


# ── commands ─────────────────────────────────────────────────────────────────


def collect_urls(a) -> list:
    raw = list(a.urls or [])
    if getattr(a, "urls_file", None):
        raw += Path(a.urls_file).expanduser().read_text().split()
    # stdin only when asked for with "-": inside an agent harness it is often a pipe that never closes.
    if raw == ["-"]:
        raw = sys.stdin.read().split() if sys.stdin else []
    seen, out = set(), []
    for u in raw:
        u = normalize_url(u)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def write_binary(e: BinaryContent, url: str, a, prefix: str = "") -> str:
    ext = {"application/pdf": "pdf", "application/zip": "zip"}.get(e.ctype) or re.sub(r"[^a-z0-9]", "", e.ctype.split("/")[-1].split("+")[0])[:8] or "bin"
    out = getattr(a, "output", None)
    name = prefix + with_ext(slug(url), ext)
    if out and not str(out).endswith("/") and not Path(out).expanduser().is_dir():
        path = Path(out).expanduser()
    else:
        path = (Path(out).expanduser() if out else OUT_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(e.data)
    return f"Binary file ({e.ctype}, {len(e.data):,} bytes) saved to: {path}"


def paginate(url: str, a, browsers: Browsers) -> tuple[str, str]:
    """Follow the --next link for up to --pages pages, accumulating rows or content."""
    chunks, rows, ext, seen = [], [], "md", set()
    for n in range(1, a.pages + 1):
        if url in seen:
            break
        seen.add(url)
        res = fetch_one(url, a, browsers)
        if res.problem or res.page is None:
            if n == 1:
                msg, code = failure_text(res)
                raise ScrapeError(msg, code)
            log(f"page {n} unavailable; stopping here")
            break
        if a.field:
            rows += extract_rows(res.page, a)
            if a.limit and len(rows) >= a.limit:
                rows = rows[: a.limit]
                log(f"page {n}: {url} (row limit reached)")
                break
        else:
            text, ext = render_page(res.page, a)
            chunks.append(text)
        nxt = field_values(res.page, a.next if _PSEUDO.search(a.next) else a.next + "::attr(href)", str(res.page.url))
        log(f"page {n}: {url}" + ("" if nxt else " (last)"))
        if not nxt:
            break
        url = nxt[0]
        if a.delay:
            time.sleep(a.delay)
    if a.field:
        return (rows_to_csv(rows), "csv") if a.csv else (json.dumps(rows, ensure_ascii=False, indent=2), "json")
    return "\n\n---\n\n".join(chunks), ext


def cmd_get(a) -> int:
    urls = collect_urls(a)
    if not urls:
        raise ScrapeError("Missing URL. Usage: scrape URL [options]", EXIT_USAGE)
    a.auth_site = site_of(urls[0])
    compile_rx(a.xhr, "--xhr")
    compile_rx(a.match, "--match")
    if a.mode in ("http", "exa", "jina") and wants_browser(a):
        raise ScrapeError(
            f"-m {a.mode} cannot be combined with browser options (--screenshot, --xhr, --scroll, --wait…): drop -m or use -m stealth",
            EXIT_USAGE,
        )
    if a.screenshot and not a.screenshot.lower().endswith((".png", ".jpg", ".jpeg")):
        raise ScrapeError("--screenshot needs a .png or .jpg file name", EXIT_USAGE)
    if a.xhr_list and not a.xhr:
        a.format = "json"
    browsers = Browsers(a, urls[0])
    try:
        if len(urls) == 1:
            return get_single(urls[0], a, browsers)
        return get_batch(urls, a, browsers)
    finally:
        browsers.close()


def get_single(url: str, a, browsers: Browsers) -> int:
    started = time.time()
    out = routed(url, a)
    if out is None:
        if a.next:
            out = paginate(url, a, browsers)
        else:
            res = fetch_one(url, a, browsers)
            if res.problem:
                msg, code = failure_text(res)
                raise ScrapeError(msg, code)
            try:
                out = render_result(res, a)
            except BinaryContent as e:
                print(write_binary(e, url, a))
                return EXIT_OK
    text, ext = out
    if not text.strip():
        raise ScrapeError("The page returned no content for that selection")
    emit(text, a, slug(url), ext)
    if a.screenshot:
        shot = Path(a.screenshot).expanduser()
        # The engine swallows page_action errors: check that the file really comes from this run.
        if not shot.exists() or shot.stat().st_mtime < started - 1:
            raise ScrapeError(f"The content was fetched, but the screenshot could not be saved to {shot}")
        print(f"Screenshot saved to: {shot}")
    return EXIT_OK


def get_batch(urls: list, a, browsers: Browsers) -> int:
    if a.screenshot or a.next:
        raise ScrapeError("--screenshot and --next only accept a single URL", EXIT_USAGE)
    outdir = Path(a.output).expanduser() if a.output else OUT_DIR / f"batch-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ScrapeError(f"With several URLs, -o must be a folder: {e}", EXIT_USAGE)
    pre = {}
    plain = [u for u in urls if not (route_for(u) and routed_allowed(a))]
    if a.mode in ("auto", "http") and not wants_browser(a) and not a.delay and len(plain) > 1:
        engine()

        def safe(u):
            try:
                return tier_http(u, a)
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=min(a.concurrency, len(plain))) as ex:
            pre = dict(zip(plain, ex.map(safe, plain)))
    rows, ok, merged, codes = [], 0, [], []
    for i, url in enumerate(urls, 1):
        if a.delay and i > 1:
            time.sleep(a.delay)
        name = f"{i:03d}-{slug(url, 60)}"
        try:
            out = routed(url, a)
            tier = "route"
            if out is None:
                res = fetch_one(url, a, browsers, pre.get(url))
                tier = res.tier
                if res.problem:
                    rows.append((i, "FAILED", tier, 0, res.problem[1], url))
                    codes.append(failure_text(res)[1])
                    continue
                if a.field and res.page is not None:
                    found = [{"_url": url, **r} for r in extract_rows(res.page, a)]
                    merged += found
                    rows.append((i, "ok", tier, len(found), f"{len(found)} rows", url))
                    ok += 1
                    continue
                out = render_result(res, a)
            text, ext = out
            file = with_ext(name, ext)
            (outdir / file).write_text(text, encoding="utf-8")
            rows.append((i, "ok", tier, len(text), file, url))
            ok += 1
        except BinaryContent as e:
            saved = write_binary(e, url, argparse.Namespace(output=str(outdir) + "/"), prefix=f"{i:03d}-")
            rows.append((i, "ok", "binary", len(e.data), Path(saved.rsplit(": ", 1)[-1]).name, url))
            ok += 1
        except ScrapeError as e:
            if e.code in (EXIT_NO_ENGINE, EXIT_USAGE):
                raise
            rows.append((i, "FAILED", "-", 0, clean(str(e))[:80], url))
            codes.append(e.code)
        except Exception as e:  # one broken URL must not take the batch down
            rows.append((i, "FAILED", "-", 0, clean(f"{type(e).__name__}: {e}")[:80], url))
            codes.append(EXIT_ERROR)
    if a.field:
        target = outdir / ("rows.csv" if a.csv else "rows.json")
        target.write_text(rows_to_csv(merged) if a.csv else json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rows extracted: {len(merged)} → {target}")
    table = ["| # | status | tier | size | file / reason | url |", "|---|---|---|---|---|---|"]
    table += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:,} | {r[4]} | {r[5]} |" for r in rows]
    (outdir / "index.md").write_text(f"# Batch · {time.strftime('%Y-%m-%d %H:%M')}\n\n" + "\n".join(table) + "\n", encoding="utf-8")
    print(f"Batch: {ok}/{len(urls)} succeeded · folder: {outdir} (index in index.md)\n")
    print("\n".join(table))
    if ok:
        return EXIT_OK
    return EXIT_BLOCKED if EXIT_BLOCKED in codes else EXIT_ERROR


def routed_allowed(a) -> bool:
    return not (getattr(a, "no_route", False) or a.mode != "auto" or wants_browser(a) or needs_dom(a))


def cmd_video(a) -> int:
    text, ext = handle_video(normalize_url(a.url), a)
    emit(text, a, slug(a.url), ext)
    return EXIT_OK


def cmd_yt_search(a) -> int:
    info = ytdlp_json(["--flat-playlist", "--dump-single-json", f"ytsearch{a.n}:{a.query}"])
    rows = []
    for e in info.get("entries") or []:
        rows.append(
            {
                "title": e.get("title"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                "channel": e.get("channel") or e.get("uploader"),
                "duration": e.get("duration"),
                "views": e.get("view_count"),
            }
        )
    if a.json:
        emit(json.dumps(rows, ensure_ascii=False, indent=2), a, "yt-search", "json")
        return EXIT_OK
    lines = []
    for i, r in enumerate(rows, 1):
        extra = [r["channel"] or "?"]
        if r["duration"]:
            extra.append(fmt_ts(r["duration"]))
        if r["views"] is not None:
            extra.append(f"{r['views']:,} views")
        lines.append(f"{i}. [{r['title']}]({r['url']}) — {' · '.join(extra)}")
    emit("\n".join(lines) + "\n", a, "yt-search")
    return EXIT_OK


def cmd_tweet(a) -> int:
    text, ext = handle_tweet(a.url, a)
    emit(text, a, "tweet", ext)
    return EXIT_OK


def cmd_search(a) -> int:
    query = f"site:{a.site} {a.query}" if a.site else a.query
    objective = a.objective or f"Find the most relevant and current pages about: {a.query}"
    log("search via Exa")
    text = exa_call("web_search_exa", {"query": query, "numResults": a.n, "objective": objective})
    if a.brief:
        text = "\n".join(ln for ln in text.splitlines() if ln.startswith(("Title:", "URL:", "Published:")) or not ln.strip())
        text = re.sub(r"\n{2,}", "\n\n", text)
    emit(text + "\n", a, "search")
    return EXIT_OK


def cmd_reddit(a) -> int:
    target = a.target
    if not re.match(r"^(https?://)?([\w-]+\.)?(reddit\.com|redd\.it)/", target):
        log("Reddit search via Exa")
        text = exa_call(
            "web_search_exa",
            {"query": f"site:reddit.com {target}", "numResults": a.n, "objective": f"Reddit threads discussing: {target}"},
        )
        emit(text + "\n", a, "reddit")
        return EXIT_OK
    # A URL takes the same path as `scrape URL`: thread via the browser (Exa as fallback) or listing via RSS.
    argv = [normalize_url(target), "--max-chars", str(a.max_chars)]
    argv += ["-o", a.output] if a.output else []
    argv += ["-q"] if a.quiet else []
    get = build_parser()[1]["get"].parse_args(argv)
    get.n = a.n
    return cmd_get(get)


def cmd_feed(a) -> int:
    url = normalize_url(a.url)
    a.auth_site = site_of(url)
    raw = fetch_bytes(url, a)
    if not re.search(rb"<(rss|feed|rdf:RDF)[\s>]", raw[:3000]):
        m = re.search(rb'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', raw, re.I)
        href = re.search(rb'href=["\']([^"\']+)', m.group(0)) if m else None
        if not href:
            raise ScrapeError("Not a feed, and the page does not advertise one (<link rel=alternate>)")
        url = urllib.parse.urljoin(url, href.group(1).decode())
        log(f"feed discovered: {url}")
        raw = fetch_bytes(url, a)
    emit(render_feed(raw, a, url), a, slug(url))
    return EXIT_OK


def cmd_map(a) -> int:
    """Discover a site's URLs through robots.txt + sitemaps (nested indexes and .gz included)."""
    base = origin_of(normalize_url(a.url))
    a.auth_site = site_of(base)
    queue = []
    try:
        robots = fetch_bytes(base + "/robots.txt", a).decode("utf-8", "replace")
        queue = [m.group(1).strip() for m in re.finditer(r"(?im)^sitemap:\s*(\S+)", robots)]
    except ScrapeError:
        pass
    queue = queue or [base + "/sitemap.xml", base + "/sitemap_index.xml", base + "/wp-sitemap.xml"]
    rx = compile_rx(a.match, "--match")
    seen, urls, fetched = set(), [], 0
    while queue and fetched < 60 and len(urls) < a.limit:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            raw = fetch_bytes(sm, a)
        except ScrapeError:
            continue
        fetched += 1
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        locs = [htmllib.unescape(m.decode("utf-8", "replace").strip()) for m in re.findall(rb"<loc>\s*(?:<!\[CDATA\[)?([^<\]]+)", raw)]
        if b"<sitemapindex" in raw[:2000]:
            queue += locs
            continue
        for u in locs:
            if (not rx or rx.search(u)) and u not in seen:
                seen.add(u)
                urls.append(u)
    if not urls:
        raise ScrapeError(f"No reachable sitemap at {base}. Alternative: scrape {base} --links --same-domain")
    urls = urls[: a.limit]
    log(f"{len(urls)} URLs from {fetched} sitemap(s)")
    text = json.dumps(urls, indent=2) if a.json else "\n".join(urls) + "\n"
    emit(text, a, f"map-{slug(base)}", "json" if a.json else "txt")
    return EXIT_OK


def cmd_crawl(a) -> int:
    """Domain-bound crawl built on the engine's site-to-Markdown spider template."""
    engine()
    from scrapling.spiders import CrawlRule, LinkExtractor, SiteToMarkdownSpider

    url = normalize_url(a.url)
    domain = site_of(url)
    for flag, patterns in (("--allow", a.allow), ("--deny", a.deny)):
        for pattern in patterns or []:
            compile_rx(pattern, flag)
    outdir = Path(a.output).expanduser() if a.output else OUT_DIR / f"crawl-{slug(domain)}-{time.strftime('%Y%m%d-%H%M%S')}"
    marker, corpus = outdir / "crawl.json", outdir / "pages.jsonl"
    if marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8")).get("domain")
        except ValueError:
            previous = None
        if previous != domain:
            raise ScrapeError(f"{outdir} holds a crawl of {previous}; use another folder with -o", EXIT_USAGE)
    elif outdir.exists() and (not outdir.is_dir() or any(outdir.iterdir())):
        raise ScrapeError(f"{outdir} already has content that is not from a previous crawl; use a new folder with -o", EXIT_USAGE)
    outdir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"domain": domain, "start_url": url}), encoding="utf-8")
    attrs = {
        "name": f"crawl-{slug(domain)}",
        "start_urls": [url],
        "allowed_domains": {domain},
        "output_dir": str(outdir / "pages"),
        "max_pages": a.max_pages,
        "robots_txt_obey": not a.ignore_robots,
        "concurrent_requests": a.concurrency,
        "download_delay": a.delay,
        "css_selector": a.selector,
        "logging_level": logging.INFO if a.verbose else logging.ERROR,
    }
    if a.allow or a.deny:
        attrs["rules"] = lambda self: [CrawlRule(LinkExtractor(allow=a.allow or (), deny=a.deny or ()))]

    async def parse(self, response):
        if self.max_pages and self._page_count >= self.max_pages and response.request is not None:
            # A response that was in flight when the cap was hit: back to the queue for the next run.
            again = response.request.copy()
            again.dont_filter = True
            yield again
            return
        async for item in SiteToMarkdownSpider.parse(self, response):
            yield item
        # Without this the template keeps downloading the whole queue even though it no longer converts pages.
        if self.max_pages and self._page_count >= self.max_pages and not getattr(self, "_cap_hit", False):
            self._cap_hit = True
            self.pause()

    async def on_scraped_item(self, item):
        item = await SiteToMarkdownSpider.on_scraped_item(self, item)
        if item is not None:
            # The corpus is written page by page: if the process dies, nothing already downloaded is lost.
            with open(corpus, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    attrs["parse"] = parse
    attrs["on_scraped_item"] = on_scraped_item
    if a.mode == "stealth":
        from scrapling.fetchers import AsyncStealthySession

        attrs["configure_sessions"] = lambda self, manager: manager.add(
            "default", AsyncStealthySession(headless=True, block_ads=True, solve_cloudflare=True, max_pages=a.concurrency)
        )
    spider = type("SiteCrawl", (SiteToMarkdownSpider,), attrs)(crawldir=str(outdir / ".checkpoint"))
    log(f"crawling {domain} (max {a.max_pages} pages, robots.txt {'ignored' if a.ignore_robots else 'obeyed'})")
    result = spider.start()
    merged = {}
    if corpus.exists():
        for line in corpus.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                merged[row["url"]] = row
            except (ValueError, KeyError, TypeError):
                continue
    items = list(merged.values())
    corpus.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
    index = [f"# Crawl of {domain}", f"{len(items)} pages · {time.strftime('%Y-%m-%d %H:%M')}", ""]
    index += [f"- [{clean(it.get('title') or it['url'])}]({it['url']}) · {len(it.get('markdown') or ''):,} characters" for it in items]
    (outdir / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    st = result.stats
    new = len(result.items)
    print(f"Crawl finished: {len(items)} pages in {outdir}" + (f" ({new} new in this run)" if new != len(items) else ""))
    print(f"- Markdown per page: {outdir / 'pages'}/")
    print(f"- Corpus JSONL (url, title, markdown): {corpus}")
    print(f"- Index: {outdir / 'index.md'}")
    print(
        f"- Requests: {st.requests_count} · failed: {st.failed_requests_count} · blocked: {st.blocked_requests_count}"
        f" · disallowed by robots.txt: {st.robots_disallowed_count} · {st.elapsed_seconds:.0f}s"
    )
    if result.paused:
        print("- URLs remain queued (--max-pages limit, or a pause): re-run with the same -o to continue from there.")
    return EXIT_OK if items else EXIT_BLOCKED


def cmd_shopify(a) -> int:
    """A Shopify store's catalogue through its public /products.json endpoint."""
    base = origin_of(normalize_url(a.store))
    a.auth_site = site_of(base)
    products = []
    for n in range(1, a.pages + 1):
        raw = fetch_bytes(f"{base}/products.json?limit=250&page={n}", a)
        try:
            batch = json.loads(raw).get("products", [])
        except (ValueError, AttributeError):
            raise ScrapeError("The store does not expose /products.json (not Shopify, or it is protected)")
        products += batch
        log(f"page {n}: {len(batch)} products")
        if len(batch) < 250:
            break
        time.sleep(a.delay)
    rows = []
    for p in products:
        vs = p.get("variants") or []
        tags = p.get("tags") or []
        common = {
            "title": p.get("title"),
            "vendor": p.get("vendor"),
            "type": p.get("product_type"),
            "tags": ", ".join(tags) if isinstance(tags, list) else tags,
            "url": f"{base}/products/{p.get('handle')}",
            "image": ((p.get("images") or [{}])[0]).get("src"),
            "published_at": p.get("published_at"),
            "updated_at": p.get("updated_at"),
        }
        if a.variants:
            for v in vs:
                rows.append({**common, "variant": v.get("title"), "sku": v.get("sku"), "price": v.get("price"), "compare_at_price": v.get("compare_at_price"), "available": v.get("available")})
        else:
            prices = [float(v["price"]) for v in vs if v.get("price")]
            rows.append({**common, "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None, "variants": len(vs), "available": any(v.get("available") for v in vs)})
    log(f"{len(products)} products · {len(rows)} rows")
    emit(rows_to_csv(rows) if a.csv else json.dumps(rows, ensure_ascii=False, indent=2), a, f"shopify-{slug(base)}", "csv" if a.csv else "json")
    return EXIT_OK


def _wait_until_closed(page, timeout_s: int, snapshot: list) -> None:
    """Wait until the user closes the window, keeping the last cookies seen."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            snapshot[:] = page.context.cookies()
            page.wait_for_timeout(1500)
        except Exception:
            return


def cmd_login(a) -> int:
    """Open a visible window on a persistent profile: the user logs in by hand and closes the window."""
    engine()
    from scrapling.fetchers import StealthySession

    name = a.name
    prof = profile_dir(name)
    url = normalize_url(a.url)
    prof.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME_DIR, 0o700)
    os.chmod(prof, 0o700)
    real = chrome_installed() and not a.chromium
    print(f"Opening {url} in a window using profile '{name}'.", flush=True)
    print(f"Log in by hand and CLOSE the window when you are done (max {a.timeout}s).", flush=True)
    snapshot, opened = [], []

    def waiter(page):
        opened.append(True)
        _wait_until_closed(page, a.timeout, snapshot)

    try:
        s = StealthySession(headless=False, user_data_dir=str(prof), real_chrome=real, retries=1)
        s.start()
        try:
            s.fetch(url, page_action=waiter, network_idle=False, timeout=(a.timeout + 30) * 1000)
        finally:
            s.close()
    except Exception as e:
        log(f"window closed ({type(e).__name__})")
    if not opened:
        raise ScrapeError("Could not open the browser window (no display, or Chromium missing?). No session was saved.")
    (prof / "scrape-profile.json").write_text(json.dumps({"url": url, "real_chrome": real, "created": time.strftime("%Y-%m-%d")}))
    # Session cookies (no expiry) do not survive the browser closing: they are stored separately.
    session_cookies = [c for c in snapshot if c.get("expires", -1) in (-1, None)]
    jar = prof / "session-cookies.json"
    jar.write_text(json.dumps(session_cookies))
    os.chmod(jar, 0o600)
    print(f"Profile saved ({len(snapshot)} cookies). Use it with: scrape URL --profile {name}")
    return EXIT_OK


def cmd_profiles(a) -> int:
    if a.action == "rm":
        if not a.name:
            raise ScrapeError("Name the profile: scrape profiles rm NAME", EXIT_USAGE)
        d = Path(profile_info(a.name)["dir"])
        if d.is_symlink() or d.resolve().parent != PROFILES_DIR.resolve():
            raise ScrapeError("Unexpected profile path; nothing was deleted")
        shutil.rmtree(d)
        print(f"Profile deleted: {a.name}")
        return EXIT_OK
    names = sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir() and _PROFILE_NAME.match(p.name)) if PROFILES_DIR.is_dir() else []
    if not names:
        print("No profiles. Create one with: scrape login NAME URL")
    for n in names:
        info = profile_info(n)
        print(f"- {n} · {info.get('url', '?')} · created {info.get('created', '?')}")
    return EXIT_OK


def cmd_clean(a) -> int:
    """Delete cached outputs (~/.scraping-eagle/out) older than N days."""
    if not OUT_DIR.is_dir():
        print("Nothing to clean.")
        return EXIT_OK
    cutoff, n = time.time() - a.days * 86400, 0
    for p in OUT_DIR.iterdir():
        try:
            if p.lstat().st_mtime >= cutoff:
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            n += 1
        except OSError as e:
            log(f"could not delete {p.name}: {e}")
    print(f"Deleted {n} outputs older than {a.days} days from {OUT_DIR}")
    return EXIT_OK


def chromium_installed() -> bool:
    roots = [Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"]
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        roots.insert(0, Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    return any(r.is_dir() and any(r.glob("chromium-*")) for r in roots)


def cmd_doctor(a) -> int:
    checks = []

    def add(group, name, state, detail="", fix=""):
        checks.append({"group": group, "name": name, "state": state, "detail": detail, "fix": fix})

    try:
        import scrapling

        add("Engine", "Scrapling", "ok", f"{scrapling.__version__} · Python {sys.version.split()[0]}")
        have_engine = True
    except ImportError:
        add("Engine", "Scrapling", "missing", "", "scrape setup")
        have_engine = False
    add("Engine", "Chromium (dynamic/stealth tiers)", "ok" if chromium_installed() else "missing", "", "" if chromium_installed() else "scrape setup")
    add("Engine", "Google Chrome (--real-chrome, login)", "ok" if chrome_installed() else "optional", "", "")

    try:
        v = run(ytdlp_base() + ["--version"], 20).stdout.strip()
        js = "deno" if shutil.which("deno") else ("node" if shutil.which("node") else "")
        add("No setup needed", "YouTube / video (yt-dlp)", "ok" if js else "warning", f"{v} · JS runtime: {js or 'none'}", "" if js else "brew install deno")
    except (ScrapeError, OSError, subprocess.SubprocessError):
        add("No setup needed", "YouTube / video (yt-dlp)", "missing", "", "scrape setup")
    try:
        import feedparser

        add("No setup needed", "RSS / Atom (feedparser)", "ok", feedparser.__version__)
    except ImportError:
        add("No setup needed", "RSS / Atom (feedparser)", "missing", "", "scrape setup")
    if shutil.which("gh"):
        authed = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
        add("No setup needed", "GitHub (gh)", "ok", "logged in" if authed else "anonymous (60 requests/h)", "" if authed else "gh auth login")
    else:
        add("No setup needed", "GitHub (public API)", "ok", "no gh: 60 requests/h", "brew install gh")

    if a.online:
        def probe(name, fn):
            t0 = time.time()
            try:
                fn()
                add("Network (live)", name, "ok", f"{time.time() - t0:.1f}s")
            except Exception as e:
                add("Network (live)", name, "failed", clean(str(e))[:90])

        if have_engine:
            engine()
            probe("HTTP engine → quotes.toscrape.com", lambda: tier_http("https://quotes.toscrape.com/", argparse.Namespace()))
        probe("Search and Reddit fallback (Exa, no key)", lambda: exa_call("web_search_exa", {"query": "example domain", "numResults": 1, "objective": "test"}, 30))
        probe("Remote reader (Jina)", lambda: jina_read("https://example.com", 30))
        probe("Single tweets (syndication)", lambda: handle_tweet("https://x.com/jack/status/20", argparse.Namespace()))

    profiles = sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir()) if PROFILES_DIR.is_dir() else []
    add("With login (optional)", "Saved browser profiles", "ok" if profiles else "none", ", ".join(profiles), "" if profiles else "scrape login NAME URL")
    for exe, label, fix in (
        ("opencli", "OpenCLI (Reddit/IG/FB/X through your Chrome)", "npm i -g @jackwener/opencli + Chrome extension"),
        ("twitter", "twitter-cli (X search/timeline)", "uv tool install twitter-cli"),
        ("rdt", "rdt-cli (Reddit with a cookie)", "see references/platforms.md"),
    ):
        add("With login (optional)", label, "ok" if shutil.which(exe) else "not installed", "", "" if shutil.which(exe) else fix)
    creds = bool(os.environ.get("TWITTER_AUTH_TOKEN") and os.environ.get("TWITTER_CT0"))
    add("With login (optional)", "X credentials in the environment", "ok" if creds else "not set", "", "")
    add("Environment", "Proxy (SCRAPE_PROXY)", "set" if os.environ.get("SCRAPE_PROXY") else "none", "", "")
    add("Environment", "Data folder", "ok", str(HOME_DIR))

    if a.json:
        print(json.dumps({"version": VERSION, "checks": checks}, ensure_ascii=False, indent=2))
        return EXIT_OK
    icons = {"ok": "✓", "missing": "✗", "failed": "✗", "warning": "!", "set": "✓"}
    print(f"Scraping Eagle {VERSION}")
    group = None
    for c in checks:
        if c["group"] != group:
            group = c["group"]
            print(f"\n{group}")
        line = f"  {icons.get(c['state'], '·')} {c['name']}: {c['state']}"
        if c["detail"]:
            line += f" ({c['detail']})"
        if c["fix"]:
            line += f"  → {c['fix']}"
        print(line)
    return EXIT_ERROR if any(c["state"] in ("missing", "failed") for c in checks) else EXIT_OK


# ── CLI ──────────────────────────────────────────────────────────────────────


def add_out(p, max_chars: int = DEFAULT_MAX_CHARS) -> None:
    p.add_argument("-o", "--output", help="save to a file (or folder/) instead of printing")
    p.add_argument("--max-chars", type=int, default=max_chars, help=f"maximum characters printed (0 = no limit; default {max_chars})")
    p.add_argument("-q", "--quiet", action="store_true", help="no diagnostics on stderr")
    p.add_argument("-v", "--verbose", action="store_true", help="show engine logs")


def add_net(p) -> None:
    p.add_argument("--timeout", type=int, default=30, help="seconds per request (default 30)")
    p.add_argument("--proxy", help="http://user:pass@host:port (or the SCRAPE_PROXY variable)")
    p.add_argument("--cookie", help='cookies "a=1; b=2"')
    p.add_argument("-H", "--header", action="append", help='header "Key: value" (repeatable)')
    p.add_argument("--impersonate", help="TLS fingerprint for the http tier: chrome, firefox, safari, edge…")


class Parser(argparse.ArgumentParser):
    # argparse exits with 2 by default, which here means "blocked".
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\nPer-command options: scrape get --help, scrape yt --help, scrape crawl --help…\n")


def build_parser():
    ap = Parser(
        prog="scrape",
        description="Scraping Eagle: `scrape URL` picks the best path on its own (a platform route, or the fetch engine with automatic escalation).",
    )
    ap.add_argument("--version", action="version", version=f"Scraping Eagle {VERSION}")
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    g = sub.add_parser("get", help="(default) read one or more URLs")
    g.add_argument("urls", nargs="*", help="URL(s); `-` reads them from stdin")
    g.add_argument("--urls-file", help="file with one URL per line")
    g.add_argument("-m", "--mode", choices=["auto", "http", "dynamic", "stealth", "exa", "jina"], default="auto", help="force a tier (default auto: http → stealth → exa → jina)")
    g.add_argument("-f", "--format", choices=["md", "text", "html", "json"], default="md")
    g.add_argument("-s", "--selector", help="CSS: only those elements; with ::text or ::attr(x) returns the list of values")
    g.add_argument("--each", help="CSS of each row for structured extraction (with --field)")
    g.add_argument("--field", action="append", help="field as name=selector (repeatable; name[] = every match)")
    g.add_argument("--csv", action="store_true", help="--field rows as CSV instead of JSON")
    g.add_argument("--limit", type=int, default=0, metavar="N", help="maximum rows (--each) or links (--links)")
    g.add_argument("--next", help="CSS of the 'next' link, to paginate")
    g.add_argument("--pages", type=int, default=5, help="maximum pages with --next (default 5)")
    g.add_argument("--outline", type=int, nargs="?", const=6, metavar="DEPTH", help="DOM skeleton for choosing selectors (depth, default 6)")
    g.add_argument("--links", action="store_true", help="list links instead of content")
    g.add_argument("--same-domain", action="store_true", help="with --links: same domain only")
    g.add_argument("--match", help="with --links: regex the URL must match")
    g.add_argument("--meta", action="store_true", help="metadata: title, description, OpenGraph, JSON-LD, feeds")
    g.add_argument("--data", action="store_true", help="embedded JSON: JSON-LD, __NEXT_DATA__, window.__STATE__")
    g.add_argument("--xhr", metavar="REGEX", help="capture XHR/fetch responses whose URL matches (hidden API)")
    g.add_argument("--xhr-list", action="store_true", help="list every XHR/fetch call the page makes")
    g.add_argument("--screenshot", metavar="PNG", help="full-page screenshot")
    g.add_argument("--scroll", type=int, default=0, metavar="N", help="scroll N times (lazy loading / infinite scroll)")
    g.add_argument("--wait-selector", help="wait until this CSS selector exists")
    g.add_argument("--wait", type=int, default=0, metavar="MS", help="extra wait after load")
    g.add_argument("--no-network-idle", action="store_true", help="do not wait for network idle (faster)")
    g.add_argument("--fast", action="store_true", help="browser without images, fonts or CSS")
    g.add_argument("--headful", action="store_true", help="visible browser window")
    g.add_argument("--real-chrome", action="store_true", help="use the installed Google Chrome")
    g.add_argument("--cdp", metavar="URL", help="connect to an already running browser (http://localhost:9222)")
    g.add_argument("--profile", help="persistent logged-in profile (see `scrape login`)")
    g.add_argument("--locale", help="language announced to the site, e.g. en-US (default: the system language)")
    g.add_argument("--raw", action="store_true", help="uncleaned page (keeps head and hidden content)")
    g.add_argument("--full-page", action="store_true", help="whole body, without focusing on <main>/<article>")
    g.add_argument("--compact", action="store_true", help="Markdown without images or link URLs (fewer tokens)")
    g.add_argument("--no-header", action="store_true", help="no Title/URL header")
    g.add_argument("--no-route", action="store_true", help="skip platform routes (force the engine)")
    g.add_argument("--local-only", action="store_true", help="never fall back to remote readers (Exa/Jina)")
    g.add_argument("--no-detect", action="store_true", help="accept the page even if it looks like a block (only the HTTP status can reject it)")
    g.add_argument("--delay", type=float, default=0, help="seconds between requests (batches and pagination)")
    g.add_argument("--concurrency", type=int, default=4, help="concurrent HTTP requests in batches (default 4)")
    add_net(g)
    add_out(g)
    g.set_defaults(func=cmd_get)

    v = sub.add_parser("yt", aliases=["video"], help="video: metadata + clean transcript (YouTube and other yt-dlp sites)")
    v.add_argument("url")
    v.add_argument("--lang", help="subtitle language to force, e.g. es (default: the video's original language)")
    v.add_argument("--no-timestamps", action="store_true")
    v.add_argument("--meta-only", action="store_true", help="metadata only, no transcript")
    v.add_argument("--comments", type=int, default=0, metavar="N", help="include the N top-voted comments")
    v.add_argument("--full", action="store_true", help="full description")
    v.add_argument("-n", type=int, default=30, help="maximum entries when the URL is a playlist")
    v.add_argument("--json", action="store_true")
    add_out(v, 80_000)
    v.set_defaults(func=cmd_video)

    ys = sub.add_parser("yt-search", help="search YouTube videos")
    ys.add_argument("query")
    ys.add_argument("-n", type=int, default=8)
    ys.add_argument("--json", action="store_true")
    add_out(ys)
    ys.set_defaults(func=cmd_yt_search)

    t = sub.add_parser("tweet", help="read a public tweet without login")
    t.add_argument("url", help="tweet URL or ID")
    t.add_argument("--json", action="store_true")
    add_out(t)
    t.set_defaults(func=cmd_tweet)

    s = sub.add_parser("search", help="semantic web search (Exa; free, no key)")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=6, help="results (default 6)")
    s.add_argument("--site", help="restrict to a domain: reddit.com, x.com, linkedin.com…")
    s.add_argument("--objective", help="what the search should prioritise (free text)")
    s.add_argument("--brief", action="store_true", help="title, URL and date only")
    add_out(s)
    s.set_defaults(func=cmd_search)

    r = sub.add_parser("reddit", help="read a Reddit thread (URL) or search threads (text)")
    r.add_argument("target", help="thread/subreddit URL, or search text")
    r.add_argument("-n", type=int, default=8, help="search results, or entries of a listing")
    add_out(r, 60_000)
    r.set_defaults(func=cmd_reddit)

    f = sub.add_parser("feed", help="read an RSS/Atom feed (or discover it from the page)")
    f.add_argument("url")
    f.add_argument("-n", type=int, default=15)
    f.add_argument("--full", action="store_true", help="full content of each entry")
    add_net(f)
    add_out(f)
    f.set_defaults(func=cmd_feed)

    m = sub.add_parser("map", help="list a site's URLs (robots.txt + sitemaps)")
    m.add_argument("url")
    m.add_argument("--match", help="regex the URLs must match")
    m.add_argument("--limit", type=int, default=2000)
    m.add_argument("--json", action="store_true")
    add_net(m)
    add_out(m, 20_000)
    m.set_defaults(func=cmd_map)

    c = sub.add_parser("crawl", help="crawl a site to Markdown (obeys robots.txt, resumable)")
    c.add_argument("url")
    c.add_argument("--max-pages", type=int, default=50)
    c.add_argument("--allow", action="append", help="regex of URLs to follow (repeatable)")
    c.add_argument("--deny", action="append", help="regex of URLs to exclude (repeatable)")
    c.add_argument("-s", "--selector", help="CSS of the content to convert on each page")
    c.add_argument("-m", "--mode", choices=["http", "stealth"], default="http")
    c.add_argument("--concurrency", type=int, default=4)
    c.add_argument("--delay", type=float, default=0.25, help="seconds between requests (default 0.25)")
    c.add_argument("--ignore-robots", action="store_true", help="only on your own sites or with explicit permission")
    c.add_argument("-o", "--output", help="output folder (reuse it to continue)")
    c.add_argument("-q", "--quiet", action="store_true")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_crawl)

    sh = sub.add_parser("shopify", help="full catalogue of a Shopify store (JSON/CSV)")
    sh.add_argument("store", help="store domain")
    sh.add_argument("--pages", type=int, default=4, help="pages of 250 products (default 4)")
    sh.add_argument("--variants", action="store_true", help="one row per variant (SKU, price, stock)")
    sh.add_argument("--csv", action="store_true")
    sh.add_argument("--delay", type=float, default=0.5)
    add_net(sh)
    add_out(sh, 20_000)
    sh.set_defaults(func=cmd_shopify)

    lg = sub.add_parser("login", help="save a session: opens a window, the user logs in by hand")
    lg.add_argument("name", help="profile name, e.g. linkedin")
    lg.add_argument("url", help="the site's login page")
    lg.add_argument("--timeout", type=int, default=600, help="maximum seconds to wait (default 600)")
    lg.add_argument("--chromium", action="store_true", help="use Chromium instead of Google Chrome")
    lg.add_argument("-q", "--quiet", action="store_true")
    lg.add_argument("-v", "--verbose", action="store_true")
    lg.set_defaults(func=cmd_login)

    pr = sub.add_parser("profiles", help="list or delete saved profiles")
    pr.add_argument("action", nargs="?", choices=["ls", "rm"], default="ls")
    pr.add_argument("name", nargs="?")
    pr.set_defaults(func=cmd_profiles)

    d = sub.add_parser("doctor", help="status of the engine and of every channel")
    d.add_argument("--online", action="store_true", help="also test the network (4 requests)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    cl = sub.add_parser("clean", help="delete old cached outputs")
    cl.add_argument("--days", type=int, default=7)
    cl.set_defaults(func=cmd_clean)

    sub.add_parser("setup", help="install or update the engine (setup --upgrade)")
    return ap, sub.choices


COMMANDS = {"get", "yt", "video", "yt-search", "tweet", "search", "reddit", "feed", "map", "crawl", "shopify", "login", "profiles", "doctor", "clean", "setup"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap, commands = build_parser()
    if not argv:
        ap.print_help()
        return EXIT_OK
    if argv[0] in ("-h", "--help", "--version"):
        ap.parse_args(argv)
    # The subcommand may follow value-less options (`scrape -q search x`); with none, it is `get`.
    lead = 0
    while lead < len(argv) and argv[lead] in ("-q", "--quiet", "-v", "--verbose"):
        lead += 1
    if lead < len(argv) and argv[lead] in commands:
        argv = [argv[lead]] + argv[:lead] + argv[lead + 1 :]
    else:
        argv.insert(0, "get")
    # `--outline URL`: without this argparse would take the URL as the depth.
    for i, tok in enumerate(argv):
        if tok == "--outline" and (i + 1 == len(argv) or not argv[i + 1].isdigit()):
            argv[i] = "--outline=6"
    cmd, parser = argv[0], commands[argv[0]]
    # Only `get` mixes URLs and options in any order.
    a = parser.parse_intermixed_args(argv[1:]) if cmd == "get" else parser.parse_args(argv[1:])
    STATE["quiet"], STATE["verbose"] = getattr(a, "quiet", False), getattr(a, "verbose", False)
    if not getattr(a, "func", None):
        print("`scrape setup` is run by the launcher: scripts/scrape setup [--upgrade]", file=sys.stderr)
        return EXIT_USAGE
    try:
        return a.func(a) or EXIT_OK
    except ScrapeError as e:
        print(f"[scrape] ERROR: {e}", file=sys.stderr)
        return e.code
    except subprocess.TimeoutExpired as e:
        print(f"[scrape] ERROR: timed out running {' '.join(Path(str(c)).name for c in e.cmd[:3])}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        if STATE["verbose"]:
            traceback.print_exc()
        print(f"[scrape] UNEXPECTED ERROR: {type(e).__name__}: {clean(str(e))[:300]} (use -v for the traceback)", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

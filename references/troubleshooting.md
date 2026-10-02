# Diagnosis and maintenance

## Reading the diagnosis

Every attempt leaves a line on stderr:

```
[scrape] http: HTTP 403 · 6,027 bytes · 0.3s · rejected: HTTP 403
[scrape] stealth: HTTP 200 · 172,474 bytes · 7.0s · ok
```

| Rejection reason | Meaning | Next tier |
|---|---|---|
| `HTTP 401/403/407/429/5xx` | the server refuses the request | yes |
| `challenge page ('…')` | title or text of a challenge (Cloudflare, "verify you are human") | yes |
| `anti-bot cloudflare/datadome/perimeterx/imperva/akamai` | WAF markers on a nearly empty page | yes |
| `no content without JavaScript` / `un-rendered SPA` | the HTML is a shell that JS fills in | yes |
| `selector '…' matched nothing` | the page arrived, but what you asked for is not there | yes, in case JS renders it |
| `redirects to a login page` | a session is needed | no: `login-sessions.md` |
| `HTTP 404/410` | the URL does not exist | no |

Exit codes: `0` ok · `1` error (404, selector matched nothing, no tier answered) · `2` blocked or
login required · `3` engine missing · `64` bad arguments.

"No tier got a response" means no page arrived at all: a domain that does not resolve, no network, a
timeout or a dropped connection. Check the URL and raise `--timeout` before thinking about anti-bot.

## Blocked after every tier

Try in this order, from cheapest to most expensive:

1. `--real-chrome`: use the installed Google Chrome instead of Chromium. A more credible fingerprint.
2. `--headful`: visible browser. Some WAFs detect windowless mode.
3. `--wait 3000` or `--wait-selector "CSS"`: give the challenge time to finish.
4. `--locale es-ES`: a language consistent with the IP.
5. Residential proxy: `--proxy http://user:pass@host:port` or the `SCRAPE_PROXY` variable. Datacenter
   IPs are the first thing sites block; a residential one is usually enough.
6. A logged-in session (`login-sessions.md`) if the content is not public.
7. After many blocks in a row on the same site, stop: insisting makes the IP's reputation worse.

If the block is about volume (429), lower the pace with `--delay` before changing anything else.

When no local route works and the URL is public, the remote readers try from another network:
`-m exa` and `-m jina`. In automatic mode they come in by themselves at the end, except in two cases
where the URL must not leave for a third party unless you ask: when no local tier got any response
from the site (usually an internal or mistyped host) and when the site answered 401 or 407 (it wants
authentication).

## False positives and negatives

- **A good page is rejected as a block**: `--no-detect` accepts any response with a successful HTTP
  status. It happens on short pages that talk about captchas or security.
- **A block page comes through as if it were good**: force the browser with `-m stealth`, or ask for
  something that only exists on the real page with `-s "selector"`. If the selector is there, the
  page is the real one.
- **The cookie notice comes out instead of the content**: `-s "main"` or the content's selector; on
  sites that render nothing until you accept, a session is needed (`--profile`).

## Empty or incomplete content

| Symptom | Usual cause | Fix |
|---|---|---|
| "no visible text is left after cleaning" | everything is hidden by CSS or behind a login | `--meta`, `--data`, `--raw`, or a session |
| Part of the text is missing | the focus picked `<main>`/`<article>` and the rest is outside | `--full-page` |
| Items missing from a listing | lazy loading | `--scroll 5`, `--wait 2000` |
| The selector fails on `http` and works on `stealth` | JavaScript renders it | normal; `-m stealth` saves the first attempt |
| Text in another language or currency | the site decides from the IP and the announced language (the system's by default) | `--locale es-ES`, or `SCRAPE_LOCALE` in the environment |
| Markdown with too much noise | menus, footers, sidebars | `-s "content selector"`, `--compact` |

## Known quirks

- **Jina returns 403 to browser User-Agents.** The CLI identifies itself as `scraping-eagle/x.y`. If
  you call `r.jina.ai` by hand, do not force a Chrome User-Agent.
- **Reddit blocks in bursts.** The anonymous RSS returns 429 after two or three requests, and the
  browser gets 403 for a few minutes if you ask for many threads in a row. The CLI then switches to
  Exa.
- **`example.com` drops the connection** for command-line HTTP clients on some networks; the stealth
  tier reads it. It is no good as a connectivity test: use `scrape doctor --online`.
- **Session cookies do not survive the browser closing**; that is why `scrape login` stores them
  separately and injects them again.
- **The engine logs "No Cloudflare challenge found" as an error** on every page without a challenge,
  and "Failed after N attempts" on every network failure. The CLI filters both and reports its own
  summary; `-v` shows the rest of the engine's logs.
- **Crawls stop at `--max-pages` on purpose.** The underlying crawl template would keep downloading
  the queue without converting it; `scrape crawl` pauses there and keeps the queue for the next run.

## Maintenance

```bash
scrape doctor              # what is installed
scrape doctor --online     # also tests the engine, Exa, Jina and tweets live
scrape setup --upgrade     # upgrades the engine, yt-dlp, feedparser and Chromium
scrape clean --days 7      # deletes old outputs from ~/.scraping-eagle/out
scrape py -m unittest discover -s ~/.claude/skills/scraping-eagle/tests    # offline tests
bash ~/.claude/skills/scraping-eagle/tests/live.sh                         # live battery (~2 min)
```

- yt-dlp needs frequent updates: it is the first suspect when YouTube fails.
- `setup` installs the 0.4.x line of the engine (`scrapling<0.5`) because the CLI relies on some of
  its internals. To move to a newer minor version, change the bound in `scripts/setup.sh` and run
  both test suites.
- On disk: `~/.scraping-eagle/venv` (engine, ~400 MB), `out/` (truncated outputs, batches, crawls),
  `profiles/` (sessions). The browser lives in Playwright's cache (`~/Library/Caches/ms-playwright`
  on macOS, `~/.cache/ms-playwright` on Linux).
- Uninstall: remove `~/.scraping-eagle`, the `~/.local/bin/scrape` link and the skill folder.

## Installation problems

| Error | Fix |
|---|---|
| `externally-managed-environment` | do not install with the system pip: `scrape setup` creates its own venv |
| No Python 3.10–3.13 | install `uv` (<https://docs.astral.sh/uv/>); `setup` downloads the Python it needs |
| `Executable doesn't exist` when opening the browser | `scrape setup --upgrade` (forces Chromium to be reinstalled) |
| `scrape: command not found` | `~/.local/bin` is not on PATH: use `~/.claude/skills/scraping-eagle/scripts/scrape` |
| No JS runtime for YouTube | `brew install deno` |

## MCP server (optional)

The engine ships an MCP server that exposes fetching, stealth fetching, sessions and screenshots as
native tools. The CLI already covers them; register it only if the user wants MCP tools:

```bash
claude mcp add scrapling -- ~/.scraping-eagle/venv/bin/scrapling-mcp
```

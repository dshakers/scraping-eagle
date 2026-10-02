<div align="center">

# 🦅 Scraping Eagle

**The web scraping skill and CLI for Claude Code and AI agents.**

One command, `scrape URL`, turns any website into clean Markdown, JSON or CSV.<br>
Past Cloudflare and JavaScript, on your own machine, with no API keys.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Tested on macOS](https://img.shields.io/badge/tested%20on-macOS-lightgrey)
![Python 3.10–3.13](https://img.shields.io/badge/python-3.10–3.13-3776AB)
![Claude Code skill](https://img.shields.io/badge/Claude%20Code-skill-D97757)
![Live checks: 23](https://img.shields.io/badge/live%20checks-23%20passing-brightgreen)

</div>

## What is Scraping Eagle?

Scraping Eagle is an open-source web scraping CLI and Claude Code skill created by
[dshakers](#author). It gives AI coding agents and developers a single command, `scrape`, that
fetches any web page, escalates on its own from a fast HTTP request to a stealth browser when a site
blocks bots, and returns clean Markdown, JSON or CSV. It runs locally, is free, and needs no API keys.

```bash
scrape https://example.com/article                        # any page → clean Markdown
scrape "https://www.youtube.com/watch?v=aircAruvnKk"      # video → metadata + transcript
scrape URL --each ".card" --field "name=h3::text" --field "price=.price::text" --csv
scrape search "best open source vector database"          # web search, no key
scrape crawl https://docs.example.com --max-pages 100     # whole site → Markdown for RAG
```

_Version 1.0.0 · every route last verified live on 2 October 2026._

## Quick start

```bash
git clone https://github.com/dshakers/scraping-eagle ~/.claude/skills/scraping-eagle
bash ~/.claude/skills/scraping-eagle/scripts/setup.sh
scrape doctor --online
```

`setup.sh` builds an isolated environment in `~/.scraping-eagle` (about 400 MB with the browser, 1–2
minutes) and links the `scrape` command into `~/.local/bin`. It never touches your system Python.

**Requirements:** `git`, and either [uv](https://docs.astral.sh/uv/) or Python 3.10–3.13. Optional:
`deno` or `node` (YouTube), `gh` (GitHub), Google Chrome (logins). Tested on macOS (Apple Silicon).
Linux should work, since the scripts have no macOS-only steps, but it has not been verified yet.

Inside Claude Code the skill triggers by itself: ask *"get the prices from this page"*, *"what does
this video say?"* or *"what are people saying on Reddit about X?"* and Claude runs `scrape` for you.

## What can it do?

| You want | Command |
|---|---|
| Read any page as clean Markdown | `scrape URL` |
| Scrape several pages at once | `scrape URL1 URL2 … -o folder/` |
| Extract a listing or table to JSON/CSV | `scrape URL --each ".row" --field "name=h3::text" --csv` |
| Follow pagination or infinite scroll | `--next "a.next" --pages 10` · `--scroll 5` |
| See a page's structure to pick selectors | `scrape URL --outline` |
| Pull embedded JSON (JSON-LD, `__NEXT_DATA__`) | `scrape URL --data` |
| Discover a site's hidden API | `scrape URL --xhr-list` |
| Take a full-page screenshot | `scrape URL --screenshot page.png` |
| Search the web | `scrape search "query"` |
| Get a YouTube transcript | `scrape VIDEO_URL` |
| Read a tweet | `scrape TWEET_URL` |
| Read a full Reddit thread | `scrape THREAD_URL` |
| Summarise a GitHub repository | `scrape https://github.com/owner/repo` |
| Read an RSS/Atom feed | `scrape feed URL` |
| List every URL of a site | `scrape map example.com` |
| Convert a whole site to Markdown | `scrape crawl URL --max-pages 50` |
| Export a Shopify catalogue | `scrape shopify store.com --csv` |
| Scrape behind a login | `scrape login NAME URL`, then `--profile NAME` |

## How do I…?

### How do I scrape a website protected by Cloudflare?

Run `scrape URL`. Scraping Eagle first sends an HTTP request with a real browser's TLS fingerprint.
If the site answers with a block or a challenge page, it switches to a stealth Chromium browser that
solves Cloudflare Turnstile and interstitial challenges automatically. On a public Turnstile demo it
returned the protected content in about 7 seconds.

```bash
scrape https://nopecha.com/demo/cloudflare
# [scrape] http: HTTP 403 · 6,027 bytes · 0.3s · rejected: HTTP 403
# [scrape] stealth: HTTP 200 · 172,474 bytes · 7.0s · ok
```

### How do I extract a product list to CSV without writing code?

Use `--each` for the row selector and one `--field name=selector` per column. `::text` returns text
and `::attr(href)` returns an attribute. Add `--next` to follow pagination and `--csv` for CSV
output. Not sure about the selectors? `scrape URL --outline` prints the page skeleton in a few lines.

```bash
scrape https://quotes.toscrape.com/ \
  --each ".quote" --field "text=.text::text" --field "author=.author::text" \
  --next "li.next a" --pages 10 --csv -o quotes.csv
```

### How do I get a YouTube transcript from the command line?

Run `scrape` with the video URL. You get the title, channel, date, duration, views, description and
the full transcript, split by chapters with timestamps, in about 5 seconds. Subtitles come in the
video's original language; `--lang es` forces another one and `--comments 20` adds top comments.

```bash
scrape "https://www.youtube.com/watch?v=aircAruvnKk"
scrape yt-search "web scraping tutorial" -n 5
```

### How do I read a full Reddit thread, with replies, without the API?

Run `scrape` with the thread URL. Reddit rejects plain HTTP clients, so Scraping Eagle opens the
thread in its stealth browser and rebuilds it as a nested list: post, comments and replies with
author and score. If Reddit blocks your IP it falls back to a remote reader automatically.

```bash
scrape "https://www.reddit.com/r/webscraping/comments/1rwefzj/"
scrape reddit "best python scraping library" -n 8      # find threads first
```

### How do I turn a whole website into Markdown for RAG?

Use `scrape crawl`. It stays on the domain, obeys robots.txt, converts every page to clean Markdown
and writes one file per page plus a `pages.jsonl` corpus (`url`, `title`, `markdown`) ready for
embedding. It stops at `--max-pages` and continues from there the next time you run it.

```bash
scrape crawl https://docs.example.com/ --max-pages 200 --allow "/docs/" -o docs/
```

### How do I find the hidden API behind a JavaScript site?

Run `scrape URL --xhr-list` to see every XHR/fetch call the page makes, then `--xhr "REGEX"` to dump
the matching responses. Calling that API directly is faster and more stable than parsing HTML.

```bash
scrape https://quotes.toscrape.com/scroll --xhr-list
scrape "https://quotes.toscrape.com/api/quotes?page=2"
```

### How do I scrape Amazon product data?

Product pages and search results on Amazon answer to the plain HTTP tier. Extract fields with
`--field`; results come back in your system language (`--locale en-GB` for another one).

```bash
scrape "https://www.amazon.es/s?k=laptop+stand" \
  --each '[data-component-type="s-search-result"]' \
  --field "asin=::attr(data-asin)" --field "title=h2 span::text" \
  --field "price=.a-price .a-offscreen::text" --field "rating=.a-icon-alt::text" --limit 10 --csv
```

### How do I scrape a site that requires a login?

Run `scrape login NAME URL`. A real browser window opens on an isolated profile, you log in by hand
and close it; from then on `scrape URL --profile NAME` reuses that session. Scraping Eagle never
types or stores passwords.

## How it works

```mermaid
flowchart TD
    A["scrape URL"] --> B{"Platform route?"}
    B -- "YouTube · tweet · GitHub repo" --> P["Native route"]
    B -- "no" --> H["http tier: browser TLS fingerprint"]
    H -- "blocked, empty or selector missing" --> S["stealth tier: patched Chromium, solves Cloudflare"]
    S -- "still blocked and the URL is public" --> R["remote readers: Exa, then Jina"]
    H -- "ok" --> O["Clean Markdown / JSON / CSV"]
    S -- "ok" --> O
    R --> O
    P --> O
```

| Tier | What it is | Typical time |
|---|---|---|
| `http` | HTTP request with Chrome's TLS fingerprint | 0.3–1.5 s |
| `stealth` | patched Chromium, solves Cloudflare Turnstile | 2–10 s |
| `exa` → `jina` | remote readers for public URLs, as a last resort | 0.3–3 s |

Each attempt is logged on stderr with the reason it was rejected, so you (or your agent) always know
which tier answered and why. Force one with `-m http|stealth|dynamic|exa|jina`.

## Supported platforms

Verified live on 2 October 2026 from a residential connection, without logging in.

| Platform | Works without login | Needs your session |
|---|---|---|
| Any website, Amazon, online stores | ✅ | — |
| YouTube | ✅ metadata, transcript, comments, search | — |
| Reddit | ✅ full threads with nested replies, search, subreddit listings | — |
| X / Twitter | ✅ single tweets, public profiles | search, threads, timeline |
| LinkedIn | ✅ public profiles, company pages, public job search | full profiles, people search |
| GitHub | ✅ repository summary + README | — |
| Shopify stores | ✅ full catalogue via the public JSON endpoint | — |
| RSS / Atom | ✅ | — |
| Instagram | metadata only (followers, posts, bio) | posts, comments |
| Facebook | — | pages, groups |

Details and limits for each one: [references/platforms.md](references/platforms.md).

## Built for AI agents

- **One command, no decisions.** `scrape URL` routes by domain and escalates by itself; the agent
  does not have to choose a tool.
- **Token discipline.** Output is capped at 40,000 characters with the full content saved to disk,
  Markdown is focused on `<main>`/`<article>`, `--compact` drops images and link URLs, and
  `--outline` describes a page's structure in a few lines instead of dumping its HTML.
- **Prompt-injection hygiene.** Scripts, hidden elements, HTML comments and zero-width characters are
  stripped before the content reaches the model.
- **Credentials stay where they belong.** `--cookie` and `-H` are sent only to the site you asked
  for, never to the third-party scripts and CDNs a page loads, and the remote readers are only used
  for public URLs that carry no tokens.
- **Predictable failures.** Exit codes (`0` ok, `1` error, `2` blocked or login required, `3` engine
  missing, `64` bad arguments) and a one-line reason for every rejected attempt.
- **Polite by default.** Crawls obey robots.txt, stay on one domain and stop at the page cap.

It ships as a Claude Code skill ([SKILL.md](SKILL.md) plus [references/](references)), and any agent
that can run shell commands (Cursor, Codex CLI, Gemini CLI, your own) can use the `scrape` command.

## Command reference

| Command | Purpose |
|---|---|
| `scrape URL…` | read pages; all extraction options (`scrape get --help`) |
| `scrape search "query"` | semantic web search (`--site`, `--brief`, `-n`) |
| `scrape yt URL` · `scrape yt-search "query"` | video metadata and transcript · video search |
| `scrape tweet URL` | a public tweet |
| `scrape reddit URL\|"topic"` | a thread, a subreddit listing, or a thread search |
| `scrape feed URL` | RSS/Atom, with feed discovery |
| `scrape map DOMAIN` | every URL from robots.txt and sitemaps |
| `scrape crawl URL` | site → Markdown corpus, resumable |
| `scrape shopify STORE` | catalogue to JSON/CSV |
| `scrape login NAME URL` · `scrape profiles` | saved browser sessions |
| `scrape doctor [--online]` · `scrape setup [--upgrade]` · `scrape clean` | health, install, cache |
| `scrape py script.py` | run your own Python with the engine available |

More: [recipes](references/recipes.md) · [logged-in sessions](references/login-sessions.md) ·
[Python guide](references/python-api.md) · [troubleshooting](references/troubleshooting.md).

## FAQ

**Is Scraping Eagle free?**
Yes. It is MIT-licensed, runs on your machine and needs no API key or account. Web search and the
remote-reader fallback use free public endpoints; optional `EXA_API_KEY` and `JINA_API_KEY`
variables raise their rate limits.

**Does it work without Claude Code?**
Yes. `scrape` is a normal command-line tool. Claude Code simply knows when to call it because the
repository is also a skill.

**Can it handle JavaScript-heavy sites?**
Yes. When the plain HTTP response is an empty shell, Scraping Eagle renders the page in a real
Chromium browser. `--wait-selector`, `--scroll` and `--wait` cover lazy content.

**Does it bypass CAPTCHAs?**
It passes Cloudflare Turnstile and interstitial challenges automatically. It does not solve image or
audio CAPTCHAs; when it meets one it reports the block instead of guessing.

**Do I need proxies?**
Usually not from a home or office connection. From datacenter IPs many sites block everything, and a
residential proxy helps: `--proxy http://user:pass@host:port` or the `SCRAPE_PROXY` variable.

**How is it different from a hosted scraping API?**
It runs locally with your own IP, costs nothing per request and keeps your data on your machine.
Hosted APIs bring managed proxy pools and scale; with Scraping Eagle you add your own proxy only when
you need one.

**What is the difference between `scrape`, `map` and `crawl`?**
`scrape URL` reads the pages you name. `map` lists a site's URLs from its sitemaps without
downloading them. `crawl` follows links inside one domain and converts every page it visits.

**Which output formats are supported?**
Markdown (default), plain text, raw HTML and JSON for pages; JSON or CSV for structured rows; PNG or
JPEG for screenshots. Binary responses such as PDFs are saved to disk.

**Is web scraping legal?**
It depends on the site, the data and your jurisdiction. Scraping Eagle is read-only and conservative
by default, but you are responsible for respecting each site's terms and privacy laws such as GDPR.
This is not legal advice.

## Requirements and dependencies

`scrape setup` installs everything into an isolated environment. Scraping Eagle orchestrates
established open-source components: [Scrapling](https://github.com/D4Vinci/Scrapling) (fetching and
parsing engine), Playwright and Patchright (Chromium automation), curl_cffi (TLS fingerprints),
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (video metadata and subtitles) and feedparser (feeds).
Search and the remote-reader fallback use the public endpoints of Exa and Jina Reader. Each keeps its
own license.

Tests: 47 offline unit tests (`scrape py -m unittest discover -s tests`) and a 23-case live battery
(`bash tests/live.sh`).

## Responsible use

Scraping Eagle only reads. Use it on content you are allowed to access, keep volumes reasonable
(`--delay`, `--max-pages`), respect robots.txt and each site's terms, and treat personal data
according to the law that applies to you. Automating a logged-in account can get it restricted.

## Author

Scraping Eagle is built and maintained by **[dshakers](https://github.com/dshakers)**.

Issues and ideas are welcome in the [issue tracker](https://github.com/dshakers/scraping-eagle/issues).
If it saves you time, a ⭐ helps other people find it.

## License

[MIT](LICENSE) © 2026 dshakers

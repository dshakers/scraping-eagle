# Writing custom scrapers in Python

Reach for Python only when the CLI cannot express the job: logic between pages, interacting with a
page, long sessions, rule-based crawls. The fetch engine under `scrape` is the open-source
[Scrapling](https://scrapling.readthedocs.io/) library, installed by `scrape setup`. Run your script
with the engine's interpreter:

```bash
scrape py my_scraper.py
```

Every snippet below, except the proxy one, was run on 2026-10-02 against engine 0.4.15. Complete,
runnable templates live in `examples/`.

## Contents

- [Choosing a fetcher](#choosing-a-fetcher)
- [The page object](#the-page-object)
- [Selecting elements](#selecting-elements)
- [Sessions](#sessions)
- [Browser options](#browser-options)
- [Acting on the page](#acting-on-the-page)
- [Capturing a hidden API](#capturing-a-hidden-api)
- [Selectors that survive redesigns](#selectors-that-survive-redesigns)
- [Async and concurrency](#async-and-concurrency)
- [Spiders](#spiders)
- [Proxies](#proxies)

## Choosing a fetcher

| Class | What it does | Use it for |
|---|---|---|
| `Fetcher` / `FetcherSession` | HTTP request with a real browser's TLS fingerprint | static pages, APIs, anything that works without JavaScript |
| `DynamicFetcher` / `DynamicSession` | Chromium driven by Playwright | JavaScript pages, automation, capturing XHR |
| `StealthyFetcher` / `StealthySession` | patched Chromium with anti-detection, solves Cloudflare | protected sites |

Start with `Fetcher`; escalate to `StealthySession` when the response is a block or an empty shell.
The two browser classes take about the same time, so there is little reason to stop at `Dynamic`
unless you need plain Playwright behaviour.

```python
from scrapling.fetchers import Fetcher, StealthyFetcher

page = Fetcher.get("https://quotes.toscrape.com/", impersonate="chrome", stealthy_headers=True, timeout=30)
page = StealthyFetcher.fetch("https://protected.example.com/", solve_cloudflare=True, network_idle=True)
```

`Fetcher` also has `post`, `put` and `delete`, and accepts `params`, `headers`, `cookies`, `proxy`,
`json` and `data`.

## The page object

Every fetcher returns the same object: a parsed document plus the response details.

```python
page.status        # 200
page.url           # final URL after redirects
page.headers       # response headers
page.cookies
page.body          # raw bytes (save binaries with this)
page.json()        # for JSON responses
page.markdown(css_selector=".quote", main_content_only=True)   # clean Markdown, hidden content removed
```

You can parse HTML you already have: `from scrapling.parser import Selector; Selector(html)`.

## Selecting elements

```python
quotes = page.css(".quote")                               # CSS
quotes = page.xpath('//div[@class="quote"]')              # XPath
quotes = page.find_all("div", class_="quote")             # by tag and attributes
author = page.find_by_text("Albert Einstein", first_match=True)

first = quotes[0]
first.css(".text::text").get()                 # first match, or None
page.css(".quote .author::text").getall()      # every match
page.css("li.next a::attr(href)").get()        # an attribute
first.css("a")[0].attrib["href"]
first.text                                     # the element's own text
first.get_all_text(strip=True)                 # text including children
first.parent, first.children                   # navigation
first.has_class("quote")
first.find_similar()                           # siblings with the same structure
page.css(".quote .text::text").re_first(r"world as (\w+)")
```

`::text` and `::attr(name)` are pseudo-elements at the end of a CSS selector, the same ones the CLI's
`-s` and `--field` options accept.

## Sessions

A session keeps one connection pool (or one browser) open across requests: faster, and cookies
persist.

```python
from scrapling.fetchers import FetcherSession, StealthySession

with FetcherSession(impersonate="chrome") as http:
    page1 = http.get("https://quotes.toscrape.com/")
    page2 = http.get("https://quotes.toscrape.com/page/2/")

with StealthySession(headless=True, block_ads=True) as browser:
    page = browser.fetch("https://quotes.toscrape.com/js/", network_idle=True)
```

Outside a `with` block, call `session.start()` and `session.close()` yourself.

## Browser options

Set when the session is created:

| Option | Effect |
|---|---|
| `headless=False` | visible window |
| `real_chrome=True` | use the installed Google Chrome |
| `user_data_dir="path"` | persistent profile (keeps logins between runs) |
| `cdp_url="http://localhost:9222"` | attach to a browser that is already running |
| `proxy="http://user:pass@host:port"` | proxy for the whole session |
| `locale="es-ES"` | browser language |
| `block_ads=True` | drop requests to known ad and tracker domains |
| `capture_xhr=r"regex"` | keep the XHR/fetch responses whose URL matches |
| `max_pages=4` | size of the tab pool for concurrent fetches |

Set per `fetch()`:

| Option | Effect |
|---|---|
| `network_idle=True` | wait until the network goes quiet |
| `wait_selector=".loaded"` | wait until that selector exists |
| `wait=2000` | extra milliseconds before reading the page |
| `timeout=60000` | milliseconds for every wait on the page |
| `disable_resources=True` | skip images, fonts and stylesheets |
| `solve_cloudflare=True` | (stealth only) solve Turnstile and interstitial challenges; costs nothing when there is none |
| `page_action=fn` | run your own function on the Playwright page after it loads |

## Acting on the page

`page_action` receives the Playwright page, so anything Playwright can do is available: clicks,
typing, scrolling, screenshots.

```python
from scrapling.fetchers import StealthySession

def go_to_next(page):
    page.click("li.next a")
    page.wait_for_load_state("domcontentloaded")

with StealthySession(headless=True) as browser:
    page = browser.fetch("https://quotes.toscrape.com/js/", page_action=go_to_next, wait_selector=".quote")
    print(page.url)          # https://quotes.toscrape.com/js/page/2/
```

Errors raised inside `page_action` are logged and swallowed, so check the result (the URL, an element
that should exist, a file that should have been written).

## Capturing a hidden API

```python
from scrapling.fetchers import DynamicSession

with DynamicSession(headless=True, capture_xhr=r"/api/quotes") as browser:
    page = browser.fetch("https://quotes.toscrape.com/scroll", network_idle=True)
    for call in page.captured_xhr:          # each one is a full response object
        print(call.status, call.url, call.json()["page"])
```

Once you know the endpoint, paginate it over plain HTTP: `examples/03_hidden_api.py`.

## Selectors that survive redesigns

Save an element's fingerprint the first time, and let the engine find it again by similarity when the
site changes its classes or structure.

```python
from scrapling.fetchers import Fetcher

Fetcher.configure(adaptive=True)
page = Fetcher.get("https://quotes.toscrape.com/")
authors = page.css(".quote .author", auto_save=True)      # first run: remember them
authors = page.css(".quote .author", adaptive=True)       # later runs: relocate if the selector breaks
```

## Async and concurrency

```python
import asyncio
from scrapling.fetchers import AsyncFetcher, AsyncStealthySession

async def main():
    page = await AsyncFetcher.get("https://quotes.toscrape.com/")
    async with AsyncStealthySession(headless=True, max_pages=2) as browser:
        urls = ["https://quotes.toscrape.com/js/", "https://quotes.toscrape.com/js/page/2/"]
        pages = await asyncio.gather(*(browser.fetch(u) for u in urls))
        return [len(p.css(".quote")) for p in pages]

print(asyncio.run(main()))    # [10, 10]
```

## Spiders

For crawls: concurrency, per-domain delays, robots.txt, retries on blocks, pause and resume.

```python
import logging
from scrapling.spiders import CrawlSpider, CrawlRule, LinkExtractor

class Authors(CrawlSpider):
    name = "authors"
    start_urls = ["https://quotes.toscrape.com/"]
    allowed_domains = {"quotes.toscrape.com"}
    robots_txt_obey = True
    concurrent_requests = 4
    download_delay = 0.2
    logging_level = logging.ERROR

    def rules(self):
        return [CrawlRule(LinkExtractor(allow=r"/author/"), callback=self.parse_author)]

    async def parse_author(self, response):
        yield {"name": response.css("h3.author-title::text").get(), "url": response.url}

result = Authors().start()
result.items.to_json("authors.json", indent=True)     # also to_jsonl() and to_csv()
print(len(result.items), result.stats.requests_count)
```

- Plain `Spider`: write `async def parse(self, response)` yourself, yield dictionaries for items and
  `response.follow(url, callback=...)` for more pages. See `examples/02_spider_with_fallback.py`.
- Several sessions in one spider (cheap HTTP first, stealth browser only for blocked requests):
  override `configure_sessions` and `retry_blocked_request`, as that example does.
- Pause and resume: `MySpider(crawldir="./state").start()`; Ctrl+C saves a checkpoint and the next
  run with the same folder continues.
- `autothrottle_enabled = True` adapts the delay per domain and backs off when the site starts
  blocking.
- Ready-made templates in `scrapling.spiders`: `SitemapSpider` (walks sitemaps),
  `SiteToMarkdownSpider` (what `scrape crawl` uses), `ShopifySpider`, `XMLFeedSpider` and
  `CSVFeedSpider`. Their options are in the library's documentation.

## Proxies

```python
from scrapling.fetchers import FetcherSession, ProxyRotator

rotator = ProxyRotator(["http://proxy1:8080", "http://user:pass@proxy2:8080"])
with FetcherSession(proxy_rotator=rotator, impersonate="chrome") as http:
    page = http.get("https://example.com/")
    print(page.meta["proxy"])       # which proxy served this request
```

Browser sessions accept `proxy_rotator` too. A single `proxy="..."` works on every fetcher.

For anything not covered here, the library's full documentation is at
<https://scrapling.readthedocs.io/>.

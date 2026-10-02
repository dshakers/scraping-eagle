"""Custom scraper: one reused session, http -> stealth escalation, and adaptive selectors.

Use this shape when the CLI is not enough: your own logic between pages, many requests to the
same site (the session reuses connections and cookies), or selectors that must survive redesigns.

    scrape py examples/01_session_with_escalation.py
"""

import json
from urllib.parse import urljoin

from scrapling.fetchers import FetcherSession, StealthySession

START = "https://quotes.toscrape.com/"
MAX_PAGES = 3


def blocked(page) -> bool:
    return page.status in (401, 403, 429, 503) or not page.css(".quote")


def parse(page) -> list[dict]:
    rows = []
    # auto_save stores the element's fingerprint; if the site renames its classes,
    # page.css(selector, adaptive=True) finds it again by similarity.
    for quote in page.css(".quote", auto_save=True):
        rows.append(
            {
                "text": quote.css(".text::text").get(),
                "author": quote.css(".author::text").get(),
                "tags": quote.css(".tag::text").getall(),
            }
        )
    return rows


def main() -> None:
    rows, url, stealth = [], START, None
    with FetcherSession(impersonate="chrome", selector_config={"adaptive": True}) as http:
        for _ in range(MAX_PAGES):
            page = http.get(url, stealthy_headers=True)
            if blocked(page):
                # The browser only starts if it is needed, and only once.
                if stealth is None:
                    stealth = StealthySession(headless=True, block_ads=True, selector_config={"adaptive": True})
                    stealth.start()
                page = stealth.fetch(url, solve_cloudflare=True, network_idle=True)
            rows += parse(page)
            nxt = page.css("li.next a::attr(href)").get()
            if not nxt:
                break
            url = urljoin(url, nxt)
    if stealth:
        stealth.close()
    print(json.dumps(rows[:2], ensure_ascii=False, indent=2))
    print(f"{len(rows)} rows")


if __name__ == "__main__":
    main()

"""Spider: concurrent crawl over cheap HTTP, retrying in a stealth browser when a request is blocked.

Obeys robots.txt, spaces requests out (download_delay; set autothrottle_enabled = True to let the
pace adapt on its own) and can be paused with Ctrl+C and resumed by running it again with the same
crawldir.

    scrape py examples/02_spider_with_fallback.py
"""

import logging

from scrapling.fetchers import AsyncStealthySession, FetcherSession
from scrapling.spiders import Request, Response, SessionManager, Spider


class QuotesSpider(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    allowed_domains = {"quotes.toscrape.com"}
    robots_txt_obey = True
    concurrent_requests = 4
    download_delay = 0.25
    max_blocked_retries = 2
    logging_level = logging.WARNING
    max_pages = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages_seen = 0

    def configure_sessions(self, manager: SessionManager) -> None:
        manager.add("http", FetcherSession(impersonate="chrome"))
        # lazy=True: the browser does not start until a request actually needs it.
        manager.add("stealth", AsyncStealthySession(headless=True, block_ads=True), lazy=True)

    async def retry_blocked_request(self, request: Request, response: Response) -> Request:
        request.sid = "stealth"
        return request

    async def parse(self, response: Response):
        self.pages_seen += 1
        for quote in response.css(".quote"):
            yield {
                "text": quote.css(".text::text").get(),
                "author": quote.css(".author::text").get(),
                "url": response.url,
            }
        nxt = response.css("li.next a::attr(href)").get()
        if nxt and self.pages_seen < self.max_pages:
            yield response.follow(nxt, callback=self.parse)


if __name__ == "__main__":
    result = QuotesSpider(crawldir="./crawl_quotes").start()
    result.items.to_jsonl("quotes.jsonl")
    stats = result.stats
    print(f"{len(result.items)} items · {stats.requests_count} requests · {stats.blocked_requests_count} blocked")

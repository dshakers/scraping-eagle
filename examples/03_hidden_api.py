"""Hidden API: use the browser once to discover the XHR call that loads the data, then call it directly.

This is the fastest and most stable route for SPAs and infinite-scroll listings: once the API is
known, you paginate over plain HTTP without rendering anything.

    scrape py examples/03_hidden_api.py
"""

import json
import re

from scrapling.fetchers import DynamicSession, FetcherSession

PAGE = "https://quotes.toscrape.com/scroll"
API_PATTERN = r"/api/quotes"
MAX_PAGES = 3


def discover() -> str:
    with DynamicSession(headless=True, capture_xhr=API_PATTERN) as browser:
        page = browser.fetch(PAGE, network_idle=True)
        if not page.captured_xhr:
            raise SystemExit("The page made no call matching the pattern")
        return str(page.captured_xhr[0].url)


def main() -> None:
    api = discover()
    template = re.sub(r"page=\d+", "page={}", api)
    rows = []
    with FetcherSession(impersonate="chrome") as http:
        for n in range(1, MAX_PAGES + 1):
            data = http.get(template.format(n)).json()
            rows += [{"text": q["text"], "author": q["author"]["name"]} for q in data["quotes"]]
            if not data.get("has_next"):
                break
    print(f"API: {api}")
    print(json.dumps(rows[:2], ensure_ascii=False, indent=2))
    print(f"{len(rows)} rows")


if __name__ == "__main__":
    main()

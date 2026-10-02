# Extraction recipes

All of them were run live on 2026-10-02. Selectors for specific sites (Amazon, LinkedIn) change over
time: if one stops returning data, `scrape URL --outline` shows the current structure.

## Contents

- [Order of attack](#order-of-attack)
- [Listing to CSV with pagination](#listing-to-csv-with-pagination)
- [HTML table](#html-table)
- [Several URLs into one CSV](#several-urls-into-one-csv)
- [Amazon](#amazon)
- [LinkedIn job postings](#linkedin-job-postings)
- [Hidden API of a single-page app](#hidden-api-of-a-single-page-app)
- [Embedded data](#embedded-data)
- [Sitemap to batch](#sitemap-to-batch)
- [Documentation site to Markdown](#documentation-site-to-markdown)
- [Research a topic across sources](#research-a-topic-across-sources)
- [Contact details from a site](#contact-details-from-a-site)
- [Watch a value](#watch-a-value)

## Order of attack

To get structured data out of a site, try in this order and keep the first that works:

1. `scrape URL --data`: is it already there as JSON-LD, `__NEXT_DATA__` or JS state?
2. `scrape URL --xhr-list`: is it loaded from an internal API you can call directly?
3. `scrape feed URL` / `scrape map domain`: is there a feed or sitemap that avoids walking listings?
4. `scrape URL --outline`, then `--each`/`--field`: CSS selectors over the HTML.
5. Python (`examples/`, `python-api.md`) when you need logic between pages.

The higher on the list, the more stable: JSON changes far less often than CSS classes.

## Listing to CSV with pagination

```bash
scrape https://quotes.toscrape.com/ \
  --each ".quote" \
  --field "text=.text::text" --field "author=.author::text" \
  --field "tags[]=.tag::text" --field "url=a::attr(href)" \
  --next "li.next a" --pages 10 --delay 1 --csv -o quotes.csv
```

- `--next` is the selector of the "next" link; it stops when the link disappears, when `--pages` is
  reached or once `--limit` rows are collected.
- Infinite scroll instead of pagination: `--scroll 5` (each scroll waits about 1 s).
- Rows with no field at all (headers, separators) are dropped automatically.

## HTML table

```bash
scrape "https://en.wikipedia.org/wiki/List_of_countries_by_population_(United_Nations)" \
  --each "table.wikitable tbody tr" \
  --field "country=td:nth-child(1)" --field "population=td:nth-child(2)" --csv
```

A selector without `::text` returns all the text of the element, children included.

## Several URLs into one CSV

With `--field` and several URLs, every row goes to a single file with a `_url` column:

```bash
scrape --urls-file products.txt \
  --field "title=h1::text" --field "price=.price::text" \
  --csv -o out/ --delay 1
# → out/rows.csv
```

## Amazon

Product page (amazon.es, http tier):

```bash
scrape "https://www.amazon.es/dp/ASIN" \
  --field "title=#productTitle::text" \
  --field "price=.a-price .a-offscreen::text" \
  --field "rating=#acrPopover::attr(title)" \
  --field "reviews=#acrCustomerReviewText::text" \
  --field "brand=#bylineInfo::text" \
  --field "bullets[]=#feature-bullets li span::text" \
  --field "image=#landingImage::attr(src)" \
  --field "asin=#ASIN::attr(value)"
```

Search results:

```bash
scrape "https://www.amazon.es/s?k=funda+iphone+15" \
  --each '[data-component-type="s-search-result"]' \
  --field "asin=::attr(data-asin)" --field "title=h2 span::text" \
  --field "price=.a-price .a-offscreen::text" --field "rating=.a-icon-alt::text" --limit 10 --csv
```

- `::attr(x)` with nothing in front reads the attribute of the row itself.
- The order is the page's order, ads included: these fields do not tell sponsored results from
  organic ones.
- Language and price format follow the system language; `--locale en-GB` for another one.
- `price` is the first price on the page, normally the buy box. With no featured offer it is empty.
- Several product pages: a list of URLs + `--delay 2`. With high volume Amazon answers with a captcha.

## LinkedIn job postings

The public job search needs no login:

```bash
scrape "https://www.linkedin.com/jobs/search?keywords=automation&location=Madrid" \
  --each ".base-search-card" \
  --field "position=.base-search-card__title::text" \
  --field "company=.base-search-card__subtitle" \
  --field "location=.job-search-card__location::text" \
  --field "date=time::attr(datetime)" \
  --field "url=a.base-card__full-link::attr(href)" --csv
```

## Hidden API of a single-page app

Many JavaScript pages load their data from a JSON API. Calling it directly is faster and more stable
than rendering.

```bash
scrape https://quotes.toscrape.com/scroll --xhr-list          # 1. which calls the page makes
scrape https://quotes.toscrape.com/scroll --xhr "/api/quotes"  # 2. body of the interesting ones
scrape "https://quotes.toscrape.com/api/quotes?page=2"         # 3. the API, direct and pageable
```

If the API requires session headers (a token, `x-requested-with`), copy them with `-H`. To paginate
with your own logic: `examples/03_hidden_api.py`.

## Embedded data

```bash
scrape URL --data -o data.json
jq '.jsonld[] | select(."@type"=="Product") | {name, offers}' data.json
```

`--data` gathers three sources: `jsonld` (schema.org: products, articles, events, recipes),
`json_scripts` (`<script type="application/json">` blocks, including Next.js' `__NEXT_DATA__`) and
`state` (`window.__SOMETHING__ = {...}` assignments). `--meta` is the short version: title,
description, OpenGraph, JSON-LD and feeds.

## Sitemap to batch

```bash
scrape map https://www.example.com --match "/blog/" -o urls.txt
scrape --urls-file urls.txt -o blog/ --delay 0.5
```

`map` reads `robots.txt`, follows nested sitemap indexes and decompresses `.gz` files. Without
`--delay`, a batch downloads the http tier in parallel (`--concurrency 4`) and opens the browser only
once, for the pages that need it. The folder gets one file per page plus `index.md`.

## Documentation site to Markdown

```bash
scrape crawl https://docs.example.com/ --max-pages 200 --allow "/docs/" --deny "/changelog/" -o docs_example/
```

It leaves `pages/*.md` (one file per page), `pages.jsonl` (`url`, `title`, `markdown`: ready for a
RAG pipeline) and `index.md`. It obeys robots.txt, stays on the domain and stops at `--max-pages`.
Running the same command again with the same `-o` continues where it left off: every run adds up to
`--max-pages` new pages to the same corpus. For JavaScript or Cloudflare sites: `-m stealth`.

`-o` must be a new folder or the folder of a previous crawl of the same domain; the CLI refuses to
write into a folder with other content. The corpus is saved page by page, so an interruption loses
nothing already downloaded.

## Research a topic across sources

Launch the searches in parallel (independent Bash calls) and synthesise at the end:

```bash
scrape search "TOPIC" -n 8
scrape reddit "TOPIC" -n 8          # then `scrape THREAD_URL` on the best 2 or 3
scrape search "TOPIC" --site x.com -n 8 --brief
scrape yt-search "TOPIC" -n 5
```

Then read only the best sources with `scrape URL --compact`. Always cite the URL behind each fact.

## Contact details from a site

```bash
scrape https://www.example.com/contact -f text --max-chars 0 -q \
  | grep -Eio "[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}" | sort -u
```

Corporate contacts only, and for a specific purpose. People's email addresses are personal data:
GDPR and local e-privacy rules apply to using them commercially.

## Watch a value

```bash
new=$(scrape URL -s ".price::text" -q | head -1)
[ "$new" != "$(cat price.txt 2>/dev/null)" ] && echo "Changed: $new" && echo "$new" > price.txt
```

To run it periodically, use a scheduled task or any automation tool that can execute a command.

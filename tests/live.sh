#!/usr/bin/env bash
# Live battery: runs every route of the CLI against real sites (~2 min, needs network).
# Use it to find out what stopped working when a platform changes.
#
#   bash tests/live.sh
set -uo pipefail

DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$DIR/scripts/scrape"
TMP="$(mktemp -d)"
pass=0; fail=0

t() { # name · expected pattern · expected exit code · scrape arguments
  local name="$1" pat="$2" want="$3"; shift 3
  "$S" "$@" > "$TMP/out.txt" 2> "$TMP/err.txt"; local rc=$?
  if [ "$rc" = "$want" ] && grep -q -E "$pat" "$TMP/out.txt" "$TMP/err.txt"; then
    echo "PASS  $name"; pass=$((pass + 1))
  else
    echo "FAIL  $name (exit $rc, expected $want)"; head -c 300 "$TMP/err.txt"; echo; fail=$((fail + 1))
  fi
}

Q="https://quotes.toscrape.com"
t "static page"         "Albert Einstein"          0  "$Q/"
t "JS page → stealth"   "stealth: HTTP 200"        0  "$Q/js/"
t "Cloudflare"          "nopecha.com/captcha"      0  https://nopecha.com/demo/cloudflare -s "#padded_content a::attr(href)" --local-only
t "fields + pagination" "J.K. Rowling"             0  "$Q/" --each ".quote" --field "author=.author::text" --next "li.next a" --pages 2 --csv
t "outline"             "div.quote ×10"            0  "$Q/" --outline
t "meta"                '"og:title"'               0  https://www.python.org/ --meta
t "xhr-list"            "api/quotes"               0  "$Q/scroll" --xhr-list
t "404"                 "does not exist"           1  "$Q/nope"
t "usage error"         "error"                    64 "$Q/" --each
t "batch"               "Batch: 2/2"               0  "$Q/" "$Q/js/" -o "$TMP/batch/"
t "youtube"             "## Transcript"            0  "https://www.youtube.com/watch?v=aircAruvnKk" --max-chars 0
t "yt-search"           "youtube.com/watch"        0  yt-search "web scraping python" -n 2
t "tweet"               "just setting up my twttr" 0  https://x.com/jack/status/20
t "reddit thread"       "u/codepoetn"              0  "https://www.reddit.com/r/webscraping/comments/1rwefzj/list_your_current_stack_for_scalable_complex_web/"
t "github repo"         "Stars"                    0  https://github.com/yt-dlp/yt-dlp
t "search"              "URL: https://"            0  search "python web scraping library" -n 2 --brief
t "feed"                "PEP"                      0  feed https://peps.python.org/peps.rss -n 2
t "map"                 "fastapi.tiangolo.com"     0  map https://fastapi.tiangolo.com --limit 3
t "crawl"               "Crawl finished: 3"        0  crawl "$Q/" --max-pages 3 -o "$TMP/crawl"
t "shopify"             "allbirds.com/products"    0  shopify allbirds.com --pages 1 --csv
t "exa (explicit)"      "Quotes"                   0  "$Q/" -m exa
t "jina (explicit)"     "Markdown Content"         0  "$Q/" -m jina
t "doctor"              "Scrapling: ok"            0  doctor

echo
echo "$pass passed · $fail failed · outputs in $TMP"
[ "$fail" -eq 0 ]

# Routes per platform

Each platform has an ordered list of routes: use the first one that works. The CLI already applies
the first of each list; the rest are the next steps when that one fails.

**Status of each fact**

- ✅ verified live on 2026-10-02 from a residential IP in Spain, without login.
- 📄 taken from the documentation of a third-party tool and **not tested here**, because it needs
  installing extra software or an account. Ask the user before installing anything.

Platforms change their defences every few months. If a ✅ route stops working, move to the next step
and update this file.

## Contents

- [General web](#general-web)
- [Web search](#web-search)
- [YouTube and video](#youtube-and-video)
- [X / Twitter](#x--twitter)
- [Reddit](#reddit)
- [LinkedIn](#linkedin)
- [Instagram and Facebook](#instagram-and-facebook)
- [GitHub](#github)
- [RSS and Atom](#rss-and-atom)
- [Amazon](#amazon)
- [Shopify and online stores](#shopify-and-online-stores)
- [Not covered](#not-covered)

## General web

| Step | Route | Status |
|---|---|---|
| 1 | `scrape URL` (http → stealth → exa → jina) | ✅ |
| 2 | `scrape URL --real-chrome --headful` | ✅ the mode works; how effective it is depends on the site |
| 3 | `scrape URL --proxy http://user:pass@host:port` | 📄 no proxy available to test |

Remote readers by hand, in case you need them outside the CLI:

```bash
curl -s "https://r.jina.ai/https://example.com/page"     # ✅ Jina Reader, no key
scrape URL -m exa                                         # ✅ Exa web_fetch_exa
```

Jina returns 403 when the User-Agent looks like a browser: call it as a tool (curl's default works).
With `JINA_API_KEY` in the environment the CLI sends it, which raises the rate limit.

## Web search

`scrape search "query" [-n 8] [--site domain] [--objective "…"] [--brief]` ✅

It talks to Exa's public MCP endpoint (`https://mcp.exa.ai/mcp`) over plain HTTP: free, no key and
nothing extra to install. If `EXA_API_KEY` is set, it is sent.

- The query works better as a description of the ideal page than as keywords.
- `--site reddit.com`, `--site x.com`, `--site linkedin.com` restrict results to a domain.
- `category:company` or `category:people` inside the query search for companies or people.
- `--objective` says what to prioritise and what to exclude (the CLI supplies a generic one).
- `--brief` keeps only title, URL and date; without it you also get the excerpts.

Tools on the server, should you call it by hand: `web_search_exa(query, numResults, objective)`
(`objective` is required) and `web_fetch_exa(urls, maxCharacters)`.

Alternative: the native WebSearch tool, when the session has it.

## YouTube and video

`scrape URL` or `scrape yt URL` ✅ · `scrape yt-search "query" -n 8` ✅

Returns title, channel, date, duration, views, likes, description, chapters and the transcript in
paragraphs with timestamps. `scrape yt URL` accepts any site yt-dlp supports, but every extractor is
independent and only YouTube is verified (TED failed on 2026-10-02 because of a bug in yt-dlp itself).

| Option | Effect |
|---|---|
| `--lang es` | force that subtitle language, even if it is an automatic translation |
| `--comments 20` | add the top-voted comments |
| `--no-timestamps` | transcript without timestamps |
| `--meta-only` | metadata only (faster) |
| `--json` | everything as JSON, transcript line by line |
| `-n 30` | maximum entries when the URL is a playlist or channel |

Without `--lang` the track in the video's original language is used, which is the most faithful:
the manual one first and, if there is none, the automatic one. Only when the video declares no
language are the system language and English tried. With `--lang`, the requested language wins. The
"Subtitles" line of the output says which track was used.

Requirements: yt-dlp (installed by `scrape setup`) and a JS runtime, `deno` or `node`. yt-dlp goes
stale quickly because YouTube changes often: if it fails, `scrape setup --upgrade`.

**Video without subtitles** (📄): download the audio and transcribe it with Whisper. It needs a Groq
key (free tier) or an OpenAI key, which the user must provide as an environment variable.

```bash
scrape py -m yt_dlp -x --audio-format mp3 -o "audio.%(ext)s" "URL"
curl -s https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer $GROQ_API_KEY" -F file=@audio.mp3 -F model=whisper-large-v3
```

**"Sign in to confirm you're not a bot"**: usually happens on server IPs. Calling yt-dlp by hand with
its `--cookies-from-browser chrome` option fixes it, but that reads the cookies of the user's browser:
only with explicit permission.

## X / Twitter

| What | Route | Status |
|---|---|---|
| One tweet (text, author, date, likes, replies, media, quoted tweet) | `scrape TWEET_URL` or `scrape tweet URL` | ✅ |
| Profile: bio, followers, post count and the recent posts shown to visitors | `scrape https://x.com/user` | ✅ |
| What is being said about a topic | `scrape search "topic" --site x.com` | ✅ |
| Native search, full threads, replies, timeline | requires login | see below |

The single tweet comes from X's public embed endpoint (`cdn.syndication.twimg.com/tweet-result`): it
does not include replies or threads, and it fails on deleted, protected or age-restricted tweets.

With a login, in this order:

1. Claude in Chrome with the user's session.
2. `scrape login x https://x.com/login`, then `scrape URL --profile x` (the mechanism is tested; it
   has not been validated against a real X account).
3. [twitter-cli](https://github.com/public-clis/twitter-cli) (📄). Install: `uv tool install twitter-cli`.
   It reads the `auth_token` and `ct0` cookies from the `TWITTER_AUTH_TOKEN` and `TWITTER_CT0`
   variables, which the user exports from their own browser.

   ```bash
   twitter search "query" -n 10        # may 404 when X changes the endpoint; retry once
   twitter tweet URL_OR_ID             # tweet with replies
   twitter article URL_OR_ID           # long-form articles
   twitter user-posts @user -n 20
   twitter user @user
   ```

4. [OpenCLI](https://github.com/jackwener/opencli) (📄): `opencli twitter search "query" -f yaml`,
   which drives the session of the user's Chrome.

X restricts accounts with heavy automated use: prefer a secondary account, a residential IP and a
slow pace.

## Reddit

| What | Route | Status |
|---|---|---|
| Read a full thread (post, comments and nested replies) | `scrape THREAD_URL` | ✅ |
| The same thread as JSON (author, depth, score, text, link) | `scrape THREAD_URL -f json` | ✅ |
| Find threads | `scrape reddit "topic" -n 8` or `scrape search "topic" --site reddit.com` | ✅ |
| Listing of a subreddit | `scrape reddit "https://www.reddit.com/r/SUB/top/?t=week" -n 10` | ✅ with a limit |

How a thread is read:

1. **Stealth browser**, directly (the http tier and the `.json` endpoints always return 403, and so
   does Jina). The thread is built from the page's `<shreddit-post>` and `<shreddit-comment>`
   components: in the test, 18 comments with text out of 31 announced, three levels deep; the missing
   ones are deleted comments or collapsed branches ("more replies"). The page is requested in
   English so the original text comes back: in other languages Reddit serves a machine translation
   (`--locale es-ES` if you want that).
2. **Exa**, when Reddit blocks the IP. It returns the post and only the top first-level comments
   (7 of 24 in the same test).

Reddit blocks in bursts: after many requests in a row from the same IP it answers 403 ("blocked by
network security") to the browser too, and recovers after a few minutes. For several threads use
`--delay 3`. The switch to Exa is automatic.

The listing uses Reddit's anonymous RSS, which tolerates very few requests: after two or three in a
row it answers 429. Wait a minute or use search.

Voting, posting or private feeds need a login (📄):

```bash
opencli reddit search "query" -f yaml      # the session of the user's Chrome
opencli reddit read POST_ID -f yaml
opencli reddit subreddit NAME -f yaml
rdt search "query" --limit 10              # rdt-cli with the reddit_session cookie
rdt read POST_ID
```

[rdt-cli](https://github.com/public-clis/rdt-cli) installs from GitHub
(`pipx install 'git+https://github.com/public-clis/rdt-cli.git'`).

## LinkedIn

| What | Route | Status |
|---|---|---|
| Public profile | `scrape https://www.linkedin.com/in/user/` | ✅ |
| Company page | `scrape https://www.linkedin.com/company/name/` | ✅ |
| Public job search | `scrape "https://www.linkedin.com/jobs/search?keywords=…&location=…"` | ✅ (recipe in `recipes.md`) |
| People or companies by topic | `scrape search "category:people …"` / `"category:company …"` | ✅ |
| Full profile, people search, connections | requires login | see below |

LinkedIn serves the public version to visitors without a session, but not every profile has it
enabled, and after several requests in a row it tends to redirect to the login wall ("authwall").
The CLI treats that redirect as "login required" and exits with code 2 (covered by the unit tests;
the authwall did not show up during the live tests).

With a login, in this order:

1. Claude in Chrome with the user's session: the safest route for the account.
2. `scrape login linkedin https://www.linkedin.com/login`, then `--profile linkedin --delay 3`.
3. [linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) (📄):
   `uvx mcp-server-linkedin@latest --login` opens a browser for the user to log in; it exposes
   `get_person_profile`, `search_people`, `get_company_profile` and `search_jobs`.

LinkedIn's terms forbid automated scraping and it restricts accounts that do it. With people's data,
privacy law applies: a specific purpose, the minimum necessary, no bulk lists without a legal basis.

## Instagram and Facebook

| What | Route | Status |
|---|---|---|
| Instagram: followers, following, post count and bio | `scrape https://www.instagram.com/user/ --meta` | ✅ |
| Instagram: posts, comments, reels | requires login | — |
| Facebook: pages, groups | requires login | — |

Without a session, Instagram only leaves useful data in the page metadata (the `description` field)
and Facebook shows the cookie notice and the login form.

With a login: Claude in Chrome first. Alternative, OpenCLI (📄), which reuses the Chrome session:

```bash
opencli instagram profile nasa -f yaml
opencli instagram user nasa --limit 12 -f yaml     # recent posts of one user
opencli instagram search "query" -f yaml           # searches users, not posts
opencli facebook search "query" -f yaml
opencli facebook profile zuck -f yaml
opencli facebook groups --limit 20 -f yaml         # only groups visible to the account
```

## GitHub

`scrape https://github.com/owner/repo` ✅ returns description, stars, forks, language, license,
topics, dates and the README. It uses `gh` when logged in, or the public API (60 requests/hour)
otherwise.

For everything else, `gh` directly:

```bash
gh search repos "query" --sort stars --limit 10
gh search code "query" --language python
gh issue list -R owner/repo --state open
gh issue view 123 -R owner/repo --comments
gh pr view 123 -R owner/repo
gh release list -R owner/repo
gh api repos/owner/repo/contents/path
```

A single file: `scrape https://raw.githubusercontent.com/owner/repo/main/path`.

## RSS and Atom

`scrape feed URL [-n 15] [--full]` ✅

It accepts the feed or the site: if the URL is an HTML page it looks for `<link rel="alternate">`
and follows the first one. `scrape URL --meta` lists every feed a page advertises.

## Amazon

Product pages and search results on amazon.es answer on the http tier ✅. Recipes with verified
selectors are in `recipes.md`. Things to know:

- Amazon decides language and price format from the `Accept-Language` header. The CLI sends the
  system language on every tier; `--locale en-GB` changes it (verified: the same product page came
  back in Spanish with `58,99€` and in English with `€58.99`).
- With high volume the "Enter the characters you see below" captcha appears; the CLI detects it and
  escalates. Use `--delay 2` or more in batches.
- Reviews beyond the first page require a login.

## Shopify and online stores

`scrape shopify store.com [--pages 4] [--variants] [--csv]` ✅

It reads the public `/products.json` endpoint (250 products per page): title, vendor, type, tags,
URL, image, prices and availability, without touching the HTML. `--variants` gives one row per
variant with SKU and price. Stores behind a password or extra protection do not expose it.

Other stores: try `scrape URL --data`. Most publish each product as JSON-LD of type `Product`, with
price, stock and ratings already structured.

## Not covered

- **Google Maps and Google results**: their terms forbid it and the defences are aggressive. Prefer
  the official APIs (Places API).
- **TikTok**: untested. yt-dlp has an extractor for single videos (`scrape yt URL`); profiles and
  search require a login.
- **Writing** (posting, commenting, liking): Scraping Eagle is read-only.

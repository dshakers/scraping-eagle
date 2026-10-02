# Sites that need a login

Fixed rule: **the user starts the session**. Do not type passwords, do not ask for them in the chat,
and do not automate login forms or captchas. Your work begins once the session exists.

## Which route

| Situation | Route |
|---|---|
| The session has `mcp__claude-in-chrome__*` tools | **Claude in Chrome**: the user's real Chrome, already logged in |
| No Chrome connected, or the job must repeat unattended | **Persistent profile** of `scrape` |
| The user already runs twitter-cli, OpenCLI or rdt-cli | Those tools (commands in `platforms.md`) |
| Only one cookie or header is needed | `--cookie "a=1; b=2"` or `-H "Authorization: Bearer …"` |

## Claude in Chrome

The route with the least risk for the account: same browser, same fingerprint and same IP as the
user. Navigate to the page and read it with the browser tools (`get_page_text`, `read_page`). Good
for occasional reading; for volume, use a persistent profile.

## Persistent profile

```bash
scrape login linkedin https://www.linkedin.com/login     # 1. opens a visible window
#    the user logs in by hand and CLOSES the window
scrape "https://www.linkedin.com/in/someone/" --profile linkedin --delay 3    # 2. reuses the session
scrape profiles                                           # saved profiles
scrape profiles rm linkedin                               # delete one
```

Profile names accept letters, digits, hyphens and underscores only.

How it works:

- `login` launches Google Chrome (or Chromium with `--chromium`) on a profile of its own in
  `~/.scraping-eagle/profiles/NAME`, separate from the everyday Chrome. It waits until the user
  closes the window or `--timeout` seconds pass (600 by default).
- Persistent cookies stay in the profile. Session cookies, which the browser discards when it
  closes, are stored separately (`session-cookies.json`, mode 600) and injected again on every use.
- `--profile NAME` opens that profile on the stealth tier, without a window. If the site detects
  windowless mode, add `--headful`.
- Two processes cannot use the same profile at once.
- If the window never opens (no display, browser missing), `login` fails without touching what was
  already saved.

Verified on 2026-10-02 with test cookies (persistent and session) and with the real window. It has
not been validated against real LinkedIn, X, Instagram or Facebook accounts: the first time, check
that the page returned is the logged-in one before launching a batch.

When the login expires, run `scrape login NAME URL` again with the same name.

## Cookies and headers by hand

```bash
scrape URL --cookie "sessionid=…; csrftoken=…"
scrape URL -H "Authorization: Bearer $TOKEN"
```

Where those credentials travel:

- Only to the site of the first URL and its subdomains. They are not sent to third-party CDNs,
  analytics or iframes the page loads, nor to the other URLs of a batch on a different domain.
- On the http tier they do not follow a redirect to another domain either.
- In the browser they do accompany redirects issued by the site itself (a Playwright limit). For
  tokens in headers, `-m http` is the safest choice; the CLI warns when it detects that case.
- With cookies, headers, a profile or a proxy, the CLI never falls back to the remote readers.

## Security

- A profile or a session cookie is equivalent to the password. Do not copy them into the project,
  do not commit them and do not print them in the conversation. Pass secrets through environment
  variables.
- `~/.scraping-eagle/` is created with mode 700.
- Do not read the cookies of the user's main browser (`--cookies-from-browser`, Chrome's stores)
  unless they ask for it explicitly.
- If the user pastes a cookie in the chat, use it, but remind them to invalidate it afterwards by
  logging out of the site.

## Risk for the account

LinkedIn, X, Instagram and Facebook forbid automation in their terms and restrict or close accounts
that they detect doing it. Before launching a job with a session:

- Tell the user about the risk and, for high volume, suggest a secondary account.
- Human pace: `--delay 3` or more, small batches, no parallelism.
- Read-only. Scraping Eagle does not post, comment or send messages.
- With people's data: a specific purpose, the minimum necessary and a legal basis (GDPR or the
  applicable privacy law).

## Third-party CLIs

Other tools solve login per platform. They are not installed with Scraping Eagle: do it only if the
user asks, and review each project's repository first.

| Tool | What it gives | Install | Session |
|---|---|---|---|
| [OpenCLI](https://github.com/jackwener/opencli) | Reddit, Instagram, Facebook, X | `npm i -g @jackwener/opencli` + Chrome extension | the user's Chrome |
| [twitter-cli](https://github.com/public-clis/twitter-cli) | X search, threads, timeline | `uv tool install twitter-cli` | cookies in `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` |
| [rdt-cli](https://github.com/public-clis/rdt-cli) | all of Reddit | `pipx install 'git+https://github.com/public-clis/rdt-cli.git'` | `reddit_session` cookie |
| [linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) | profiles, people, companies, jobs | `uvx mcp-server-linkedin@latest --login` | the server's own profile |

`scrape doctor` shows which ones are installed.

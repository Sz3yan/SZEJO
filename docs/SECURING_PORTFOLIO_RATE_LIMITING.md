# Securing Portfolio: Rate-Limiting via Mainframe Anon Tokens

## Why this exists

sz3yan.com is content-read-only (no more `/owner` editor — content now lives in
PayloadCMS at `cms.sz3yan.com`, served by a fully public, unauthenticated REST
API). One visitor-facing feature on the site still makes live calls to the
**portfolio's own backend**, not the CMS:

- FAQ chat box → `POST /api/chat` (when enabled)

Freelance contact no longer hits the portfolio backend: the inquiry form and
SMTP path were replaced by **PostHog Support** (`posthog.conversations` on the
client). Abuse controls for that path live in PostHog (rate limits + inbox),
not Mainframe anon tokens.

Without a gate, chat would be free-for-anyone to script-hammer. Mainframe (the
SZEJO identity provider) is the thing that issues short-lived tokens and lets
the portfolio backend tell "real browser session" apart from a bot blasting
requests.

## Flow

```
1. Browser          → portfolio backend   GET /api/auth/anon-token
2. portfolio backend → Mainframe          POST /token (client_credentials,
                                           client_id=portfolio-anon + secret)
3. Mainframe         → portfolio backend  short-lived JWT (5 min TTL)
4. portfolio backend → Browser            hands over the JWT
5. Browser           → portfolio backend  POST /api/chat,
                                           Authorization: Bearer <JWT>
```

The `client_secret` for `portfolio-anon` only ever touches step 2 — server to
server. The browser never sees it; it only ever holds the short-lived JWT.

## The two pieces in Mainframe

**`portfolio-anon` OAuth client** — confidential client, `client_credentials`
grant, scopes `chat:invoke` (+ historically `inquiry:create`, now unused).
This is the credential the portfolio backend presents to Mainframe in step 2
above. Defined/provisioned via
`core/backend/mainframe/app/api/v1/admin/portfolio_service_account.py`.

**"Portfolio Service Account" user** (`portfolio.serviceaccount@sz3yan.com`)
— not a login account, no sign-in flow uses it. Pure schema plumbing:
Mainframe's `OAuthClient.owner_id` column is `NOT NULL` — every client row
must point to *some* `User` row. Humans register most clients (e.g. you
registering Console as superadmin); `portfolio-anon` is provisioned by the
platform itself, not by a person, so this placeholder user exists solely to
satisfy that foreign key. It does nothing else.

## What got removed, and why this didn't

The owner half of this same flow — `portfolio-owner` OAuth client, PKCE
browser login, `/owner`, `/auth/callback` — was deleted entirely when
PayloadCMS replaced the hand-rolled thoughts/FAQ editor. See the mainframe
client manifest (`core/backend/mainframe/clients/portfolio.yml`) and
`scripts/remove_portfolio_owner_client.py` for that cleanup.

The freelance inquiry route (`POST /api/inquiry`) and portfolio SMTP usage
were removed when contact moved to PostHog Support. The anon client and
service account documented here are **unrelated to content authorship** and
stay for chat abuse gating (and any future portfolio-scoped APIs). CMS reads
(`cms.sz3yan.com/api/thoughts`, `/api/faqs`) need zero authentication; they're
public by design.

## SMTP note

Control-plane `szejo-control-plane-smtp` / `SMTP_RELAY_*` remain for
**Mainframe** email (password resets, etc.). Portfolio no longer depends on
that relay.

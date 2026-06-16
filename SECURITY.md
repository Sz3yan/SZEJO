# SECURITY.md

---

## 1. Broken Access Control (BOLA / IDOR)

**Rule:** Every route that loads or mutates a resource by a client-supplied ID must verify the caller is authorized for that specific resource server-side.

**Must:**
- Resolve the acting principal from a verified session/JWT, not from a request body or query param.
- For multi-tenant data (MWDB samples, mainframe identities, portfolio inquiries), filter every query by the principal's tenant/owner in the same SQL statement that fetches the row — never fetch then check in Python.
- Return `404` (not `403`) when the caller is not authorized to even know the resource exists.

**Never:**
- Trust `user_id`, `tenant_id`, `org_id`, or `owner_sub` from the request body.
- Add an admin override that reads a flag from the JWT without verifying the JWT was signed by the trusted issuer and the `aud`/`iss` match.

**Repo touchpoints:**
- `szejo-portfolio/api/app/routes/*.py`
- `szejo-control-plane/mainframe/app/api/v1/*.py`
- `szejo-atlas/mwdb-core/mwdb/resources/*.py`

---

## 2. Business Logic & Validation

**Rule:** Every inbound payload is parsed by a Pydantic model with explicit bounds; multi-step flows re-verify state server-side at each step.

**Must:**
- Define `min_length`, `max_length`, numeric ranges, regex constraints, and enum types on every Pydantic field that accepts user input.
- Reject (not silently truncate) oversized or malformed inputs with a `422`.
- For multi-step flows (OAuth/PKCE, passkey registration, anonymous token mint, inquiry submission), re-check the server-side state record at each step before advancing — do not trust client-held continuation tokens to encode state.
- Enforce per-client / per-IP rate limits on every endpoint that mutates state or mints credentials.

**Never:**
- Accept `Any`, untyped `dict`, or unbounded `str` from the network.
- Ship a "convenience" endpoint that skips validation because it's "internal-only" — assume every endpoint is reachable from the internet.

**Repo touchpoints:**
- `szejo-portfolio/api/app/schemas.py`
- `szejo-control-plane/mainframe/app/schemas/`
- Rate-limit helpers already used by `/chat`, `/inquiry`, `/auth/anon-token` (see `szejo-portfolio/docs/security-hardening.md`).

---

## 3. Code & Command Injection

**Rule:** No request-derived data is ever passed through a shell, `eval`, `exec`, or dynamic import.

**Must:**
- Use `subprocess.run([...], shell=False)` with a literal-path binary and argv list.
- Validate any path/filename argument against an allowlist or regex before passing to a subprocess.
- For Karton workers that invoke external binaries (sigcheck, ClamAV, yara, etc.), keep the binary path constant and only pass the sample file path — never user-supplied flags.

**Never:**
- `shell=True`, `os.system`, `os.popen`, `commands.getoutput`.
- `eval`, `exec`, `compile`, `__import__` on anything that touched the request body.
- String-concatenated commands like `f"clamscan {path}"`.

**Repo touchpoints:**
- `szejo-atlas/karton/workers/static/sigcheck/sigcheck.py`
- `szejo-atlas/karton/workers/static/clamav/karton-clamav.py`
- `szejo-control-plane/manifest/scripts/bootstrap.py` (deployment-only; still no shell=True)

---

## 4. SQL & Database Injection

**Rule:** All database access uses parameterized queries via SQLAlchemy ORM or `text(...)` with bound parameters. No interpolation.

**Must:**
- Prefer SQLAlchemy ORM expressions (`select(Model).where(Model.id == id)`).
- When raw SQL is required, use `text("... :name ...")` with a parameter mapping, never f-strings or `%`-formatting.
- Apply the same discipline to LDAP filters, XPath expressions, MongoDB queries, and Redis Lua scripts.

**Never:**
- `session.execute(text(f"SELECT … {user_input} …"))`
- Build a `WHERE` clause by string concatenation, even for "trusted" admin tooling.
- Disable the ORM's parameter binding to "improve performance."

**Repo touchpoints:**
- `szejo-portfolio/api/app/database.py`
- `szejo-control-plane/mainframe/app/core/database.py`
- `szejo-atlas/mwdb-core/mwdb/core/database.py`

---

## 5. LLM & Prompt Injection

**Rule:** All user-supplied chat, transcript, or document text is untrusted data. System prompts are assembled server-side and never include privileged content the user could exfiltrate.

**Must:**
- Cap user input size before constructing the prompt (size limits already enforced via Pydantic — keep them).
- Place the system prompt and any retrieved trusted context above the user message, and clearly delimit the user portion (e.g. with a fenced marker) so downstream models can be evaluated for refusal.
- Treat tool/function-call arguments produced by the model as untrusted — re-validate them on the server before execution.
- For transcription (Whisper), treat the transcript as user input downstream — do not chain it directly into a privileged prompt without the same validation.

**Never:**
- Include API keys, other users' data, secrets, or full database rows in the LLM context window.
- Reflect raw user text into the *system* role of a chat request.
- Let an LLM-suggested URL, command, or SQL string execute without server-side validation against the same allowlists used for human input.

**Repo touchpoints:**
- `szejo-portfolio/api/app/routes/chat.py`
- `szejo-portfolio/api/app/chat_faq_match.py`
- `szejo-portfolio/api/app/routes/transcribe.py`

---

## 6. Server-Side Request Forgery (SSRF)

**Rule:** Outbound HTTP destinations are picked from a server-side allowlist. User input never becomes the host of an outbound request.

**Must:**
- Define an explicit allowlist of upstream hosts (OIDC issuer, LLM provider, mail relay, etc.) loaded from config, and validate any outbound URL against it.
- Resolve the target hostname and reject if it resolves to RFC1918 (`10/8`, `172.16/12`, `192.168/16`), loopback (`127/8`, `::1`), link-local (`169.254/16`, `fe80::/10`), or cloud metadata endpoints (`169.254.169.254`).
- Disable automatic redirect following on HTTP clients used with user-derived URLs, or re-validate the redirect target against the allowlist.
- Use a short outbound timeout (≤ 10 s by default).

**Never:**
- Accept a full URL from the request body and `httpx.get(that_url)`.
- Follow redirects across origins on user-influenced fetches.

**Repo touchpoints:**
- `szejo-portfolio/api/app/routes/chat.py`
- `szejo-portfolio/api/app/routes/ops.py`
- `szejo-control-plane/mainframe/app/api/v1/oidc.py`
- `szejo-control-plane/mainframe/app/api/v1/oauth.py`

---

## 7. Authentication & Session Management

**Rule:** Authentication is enforced server-side on every protected route. Tokens are short-lived, validated strictly, and bound to the channel they were issued on.

**Must:**
- Validate JWTs with an explicit `algorithms=[...]` list (no `none`, no `HS256`/`RS256` confusion).
- Verify `iss`, `aud`, `exp`, and `nbf` on every JWT; reject tokens issued by anything other than the trusted issuer.
- Keep access tokens at ≤ 24 h (mainframe was fixed from 60 s to 24 h; do not regress in either direction).
- Apply per-IP rate limits on `/auth/*`, token mint, and PKCE callback endpoints.
- Cookies that carry session material must be `Secure`, `HttpOnly`, `SameSite=Lax` or stricter, and scoped to the correct domain.
- PKCE `state` must match exactly, be single-use, and be cleared on success and on mismatch.
- WebAuthn RP ID is pinned to `sz3yan.com` — do not introduce a deployment that uses a different RP ID without coordinated passkey migration.
- Owner login errors return stable client-safe messages; upstream provider response bodies stay in server logs only.

**Never:**
- `jwt.decode(token, key, options={"verify_signature": False})` outside of a clearly labelled, never-shipped debug tool.
- Implement a "remember me" mechanism by extending JWT lifetime past 24 h — use a refresh-token flow.
- Trust `X-Forwarded-For` from peers outside `PORTFOLIO_TRUSTED_PROXY_CIDRS`.

**Repo touchpoints:**
- `szejo-portfolio/api/app/auth.py`, `szejo-portfolio/api/app/routes/auth.py`
- `szejo-control-plane/mainframe/app/api/v1/auth.py`
- `szejo-control-plane/mainframe/app/api/v1/oauth.py`
- `szejo-control-plane/mainframe/app/api/v1/passkeys.py`

---

## 8. Client-Side Attacks (XSS / CSRF / Open Redirect / Cache Poisoning)

**Rule:** User-supplied content is rendered as data, not as code. State-changing requests carry a same-site or bearer credential. Redirect targets are validated.

**Must:**
- Pass user content through React/JSX's default escaping. If `dangerouslySetInnerHTML` is genuinely required, sanitize via a maintained library (e.g. DOMPurify) and document the reason inline.
- HTML email templates HTML-escape user-controlled fields; CR/LF in header-bound fields is rejected at the schema layer (already in place — keep it).
- For state-changing endpoints: require either a bearer token from `Authorization`, or a same-site cookie *plus* a CSRF token / origin check.
- Validate any post-login or post-logout redirect target against an allowlist of own origins.
- Set `Vary: Origin, Cookie, Authorization` on responses whose content depends on those headers, to prevent shared-cache poisoning.

**Never:**
- Construct a URL via string concatenation of user input and `window.location = ...`.
- Reflect a `Host` or `X-Forwarded-Host` header into a server-rendered link without validation.

**Repo touchpoints:**
- `szejo-portfolio/src/` (JSX entry: `src/main.jsx`)
- `szejo-control-plane/mainframe/mainframe-ui/app/` (Next.js)
- `szejo-atlas/atlas/src/` (React/TS)

---

## 9. Insecure Deserialization & SSTI

**Rule:** Untrusted data is never fed to a deserializer or a templating engine that can execute code.

**Must:**
- Use `yaml.safe_load` (never `yaml.load`), `json.loads`, and Pydantic for inbound data.
- Treat YAML manifests checked into the repo (e.g. `atlas.yml`, `portfolio.yml`) as trusted input loaded at boot — never accept YAML over the wire.
- When rendering Jinja or similar templates, pass user data as template *variables*, never as the template *source*. Autoescape stays on.

**Never:**
- `pickle.loads`, `marshal.loads`, `shelve`, or `dill` on data that crossed a trust boundary.
- `Template(user_input).render(...)` — that is server-side template injection.

**Repo touchpoints:**
- `szejo-portfolio/api/app/faq_data.py`
- `szejo-portfolio/api/app/seed.py`
- Any email template rendering in `szejo-portfolio/api/app/`.

---

## 10. Files & Misconfigurations (LFI / Upload / Path / Listing / Errors)

**Rule:** Uploaded and user-named files live under a fixed base directory with server-generated names. Errors returned to the client are stable strings.

**Must:**
- Generate the on-disk filename server-side (UUID, hash) — discard the client-supplied name except for display.
- Resolve any constructed path via `Path(base, name).resolve()` and verify `resolved.is_relative_to(base)` before opening it.
- Enforce upload size limits and content-type checks before reading the body into memory (FastAPI `UploadFile` makes this straightforward).
- Disable directory listings on any static-serving deployment.
- Client error responses are stable strings; stack traces, upstream provider bodies, and `repr(exception)` go to server logs with a request ID only (already established for portfolio JWT, auth, Whisper, chat — keep this pattern).

**Never:**
- `open(f"/uploads/{request.filename}")` — that's LFI.
- Return `traceback.format_exc()` in an HTTP response.

**Repo touchpoints:**
- `szejo-portfolio/api/app/routes/transcribe.py`
- `szejo-atlas/mwdb-core/mwdb/core/app.py`

---

## 11. Secrets & Cryptography

**Rule:** No credentials, tokens, or private keys live in source. Cryptographic primitives are current and used correctly.

**Must:**
- Load secrets from environment variables via Pydantic Settings / project config modules; in production, env vars come from Infisical and the Cloudflare/Terraform secret stores.
- Use argon2id or bcrypt for password hashing (never SHA/MD5 of password).
- JWT signing keys are ≥ 256 bits, asymmetric where the verifier is a separate service.
- Validate JWT signature *and* `iss`/`aud`/`exp`/`nbf` together; never trust an unverified `kid`.
- TLS terminates at trusted reverse proxies; service-to-service inside the Docker network is acceptable but document it.

**Never:**
- Commit `.env`, `secrets.yml`, private keys, Cloudflare API tokens, or `tfvars` with real values.
- Log full bearer tokens, JWTs, passkey assertions, or PII; log a prefix + length for debugging instead.
- Reuse a JWT signing key as an encryption key, or vice versa.

**Repo touchpoints:**
- `szejo-portfolio/api/app/` config and `szejo-portfolio/.env` (template only)
- `szejo-control-plane/mainframe/app/core/config.py`
- `szejo-atlas/mwdb-core/mwdb/core/config.py`
- Reminder: when rotating the Cloudflare token, update tfvars too — stale tfvars overrode `TF_VAR_*` from the secret store in a past incident.

---

## 12. Hardening (CORS / TLS / Headers / GraphQL)

**Rule:** Defensive controls — CORS, TLS, security headers, GraphQL limits — are configured deny-by-default and reviewed on every deploy-shape change.

**Must:**
- CORS allowlist defaults to `https://sz3yan.com`, `https://www.sz3yan.com`, `https://dev.sz3yan.com`. New origins are added via `PORTFOLIO_CORS_ORIGINS` (comma-separated), never by widening to `*`.
- `Access-Control-Allow-Credentials: true` is only set when the matching origin is on the allowlist — never with `*`.
- Edge proxy (Traefik / Cloudflare) enforces HTTPS redirect, HSTS (`max-age` ≥ 6 months), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, and a `Content-Security-Policy` that disallows inline scripts in production.
- TLS terminates only at trusted reverse proxies; uvicorn / FastAPI listens with proxy header support, and forwarded-IP headers are accepted only from `PORTFOLIO_TRUSTED_PROXY_CIDRS`.
- If a GraphQL surface is added, enable depth + complexity limits, disable introspection in production, and apply the same auth/rate-limit rules as REST.

**Never:**
- Ship a build with `CORSMiddleware(allow_origins=["*"], allow_credentials=True)`.
- Disable TLS verification on outbound HTTP clients ("just for now") in code that lands on `production`.

**Repo touchpoints:**
- `szejo-portfolio/api/app/main.py` (CORS, proxy headers)
- `szejo-control-plane/mainframe/app/main.py`
- `szejo-atlas/mwdb-core/mwdb/app.py`
- Companion record: `szejo-portfolio/docs/security-hardening.md`.

---

## Workflow expectations for Claude

When a change touches any surface above:

1. Name the category (e.g. "this is a §4 SQLi-relevant change").
2. State the prevention measure in the same response as the diff.
3. If a control cannot be applied in this change (e.g. rate limiting requires Redis and we're in single-process dev), call it out as a known gap with a follow-up, not a silent omission.
4. Prefer extending existing patterns (Pydantic schemas, allowlists, parameterized queries) over inventing new ones.

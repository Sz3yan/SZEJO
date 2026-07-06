# Runbook — JWT signing via Janus (Phase 6, default with local fallback)

Mainframe's ES256 token signing runs through the Janus KMS so the JWT private
key normally never leaves it. **On by default** (`JWT_SIGNER=janus`) with an
**automatic fallback** to the on-disk key: if Janus is unreachable, sealed, or
the `jwt-mainframe` key/token isn't provisioned yet, mainframe signs locally
and logs **CRITICAL** — token issuance (login/refresh/authorize) never goes
down because of Janus.

Code: mainframe `app/core/janus_jwt.py` + `app/core/tokens.py` (`_sign_jwt`).

## ⚠ Read first — what the fallback costs

The fallback means the local on-disk key stays trusted in the JWKS at all
times. A stolen disk key can therefore still forge platform tokens — `janus`
mode reduces *routine use* of the disk key, it does not remove it from the
trust path. That is the availability-over-strict-custody trade-off chosen
here. (The old strict mode — Janus as a hard dependency, no fallback — is not
offered; set `JWT_SIGNER=local` if you want to stop calling Janus entirely.)

Fallback triggers are loud, never silent:
- boot: `JWT_SIGNER=janus but the Janus key is unavailable … signing with the
  local on-disk key until the next restart` (stays local until restart,
  because the Janus public key must be in JWKS before it can sign)
- runtime: `Janus signing failed … falling back to the local on-disk key for
  this token` (per token; recovers by itself when Janus returns)

Watch for them: `docker logs szejo-control-plane-mainframe 2>&1 | grep CRITICAL`

## Provision (one-time)

```bash
szejo janus enable-jwt
```

It mints `MAINFRAME_JANUS_TOKEN` via the platform-svc client if needed,
idempotently creates the `jwt-mainframe` ES256 key in Janus, sets
`JWT_SIGNER=janus` in the pass store (normalises any stored `local`
override), recreates mainframe, and verifies `kid=mainframe-janus-1` appears
in the JWKS. Until this has run, mainframe simply keeps signing locally
(CRITICAL log on every boot).

On boot in `janus` mode mainframe publishes **both** keys in its JWKS — the
Janus key (`kid=mainframe-janus-1`, the active signer) and the on-disk key —
so tokens issued before the cutover or during a fallback window still verify.

## Verify

```bash
# New tokens carry the Janus kid; JWKS lists it.
curl -fsS https://mainframe.sz3yan.com/.well-known/jwks.json | jq '.keys[].kid'
#   → includes "mainframe-janus-1"
# Mint a token (e.g. login) and confirm its header kid + that it verifies via JWKS.
# No CRITICAL fallback lines in the mainframe logs.
```

## Opt out (never call Janus)

```bash
szejo secrets set JWT_SIGNER local
szejo secrets run -- docker compose up -d szejo-control-plane-mainframe
```

The on-disk key resumes signing immediately. Because both public keys stay in
JWKS during the transition, tokens signed under either key keep verifying —
no re-login, no token migration, in either direction.

## Key rotation

`janus` mode signs with the Janus key's primary version. After rotating it in
Janus, recreate mainframe so it re-fetches the public key. For zero-downtime
rotation, keep the old public key in JWKS until old tokens expire. Rotating
the *local* key (`rotate_keys`) changes only the fallback signer; the Janus
kid stays active.

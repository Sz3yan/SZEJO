# Runbook — JWT signing via Janus (Phase 6, opt-in)

Move mainframe's ES256 token signing into the Janus KMS so the JWT private key
never leaves it. **Off by default** (`JWT_SIGNER=local`). Flipping to `janus`
makes Janus a **hard dependency of token issuance**.

Code: mainframe `app/core/janus_jwt.py` + `token_service._sign_jwt`.

## ⚠ Read first — this is a SPOF

In `janus` mode, every ID/access/session token is signed by calling Janus. If
Janus is **unreachable or sealed**, token endpoints fail (login/refresh/authorize
return errors). There is intentionally **no local fallback** — that is the
"key never leaves the KMS" trade-off. Keep `local` unless you accept this.

## Enable

```bash
# 1. Create the JWT signing key in Janus (ES256). MF_TOKEN = mainframe token, scope janus:manage.
curl -XPOST http://szejo-control-plane-janus:8000/v1/keys \
  -H "Authorization: Bearer $MF_TOKEN" -H 'content-type: application/json' \
  -d '{"name":"jwt-mainframe","key_type":"ES256","purpose":"jwt"}'

# 2. Give mainframe a janus:manage token + flip the flag.
szejo secrets set MAINFRAME_JANUS_TOKEN '<token>'
szejo secrets set JWT_SIGNER janus

# 3. Recreate mainframe (re-reads env; fetches the Janus public key into JWKS).
szejo secrets run -- docker compose up -d szejo-control-plane-mainframe
```

On boot in `janus` mode mainframe publishes **both** keys in its JWKS — the new
Janus key (`kid=mainframe-janus-1`, now the active signer) and the previous
on-disk key — so tokens issued before the cutover still verify (rotation overlap).

## Verify

```bash
# New tokens carry the Janus kid; JWKS lists it.
curl -fsS https://mainframe.sz3yan.com/.well-known/jwks.json | jq '.keys[].kid'
#   → includes "mainframe-janus-1"
# Mint a token (e.g. login) and confirm its header kid + that it verifies via JWKS.
```

## Rollback (instant)

```bash
szejo secrets set JWT_SIGNER local
szejo secrets run -- docker compose up -d szejo-control-plane-mainframe
```
The on-disk key resumes signing immediately. Because both public keys stay in
JWKS during the transition, tokens signed under either key keep verifying.

## Key rotation

`janus` mode signs with the Janus key's primary version. After rotating it in
Janus, recreate mainframe so it re-fetches the public key. For zero-downtime
rotation, keep the old public key in JWKS until old tokens expire.

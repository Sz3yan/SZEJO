# Coder OIDC ↔ Mainframe connect failure — root cause + fix

## Symptom
Coder OIDC sign-in against Mainframe (`coder.sz3yan.com` → `mainframe.sz3yan.com`)
fails at token exchange. Coder logs show an OIDC error around the `/token` call
(`invalid_client` / unauthorized client), not at `/authorize`.

## Root cause
`coder` is registered as a **confidential** OAuth client
(`core/mainframe/clients/coder.yml`, `client_type: confidential`,
`client_secret_env: CODER_OIDC_CLIENT_SECRET`).

On boot, Mainframe provisions clients from `*.yml` manifests
(`app/main.py:_provision_manifest_client`). For confidential clients it reads
the plaintext secret from the env var named in `client_secret_env`, hashes it
(Argon2id), and stores the hash on the client row. If that env var is unset
inside the **Mainframe container**, the secret is silently left `NULL`:

```python
if client_type == "confidential" and secret_env:
    plaintext = os.environ.get(secret_env, "")
    if plaintext:
        hashed_secret = hash_client_secret(plaintext)
    else:
        print(f"[provision] WARNING: {client_id}: env var {secret_env} not set — "
              "client secret will be unset")
```

`docker-compose.identity.yml` wires `CODER_OIDC_CLIENT_SECRET` into the
**Coder** container's environment (`docker-compose.coder.yml`), but never into
the **Mainframe** container's environment — unlike the other two confidential
clients, which are wired correctly:

```yaml
- ATLAS_MWDB_OAUTH_CLIENT_SECRET=${ATLAS_MWDB_OAUTH_CLIENT_SECRET}
- PORTFOLIO_ANON_CLIENT_SECRET=${PORTFOLIO_ANON_CLIENT_SECRET}
# CODER_OIDC_CLIENT_SECRET was missing here
```

Result: Mainframe's `coder` client row has `client_secret = NULL`. At token
exchange, `AuthorizationService.validate_client_credentials` runs:

```python
if not client_secret or not await verify_client_secret_async(
    client_secret, client.client_secret
):
    return None
```

`verify_client_secret_async(<coder's real secret>, None)` always fails →
`validate_client_credentials` returns `None` → `/token` rejects the exchange
every single time, regardless of how correct Coder's config is. This is a
server-side provisioning gap, not a Coder misconfiguration.

## Fix
Add the missing env wiring in `docker-compose.identity.yml`
(mainframe service `environment:` block):

```yaml
- CODER_OIDC_CLIENT_SECRET=${CODER_OIDC_CLIENT_SECRET}
```

(Same `.env.enc` / `.env.template` value already exists — it just wasn't
reaching the Mainframe container.)

## Rollout steps (required — code fix alone does not patch the DB)
1. Confirm `CODER_OIDC_CLIENT_SECRET` is present and correct in the decrypted
   env used at deploy time (`szejo secrets run -- ...` / `.env.enc`).
2. Recreate the Mainframe container so it picks up the new env var:
   ```bash
   docker compose -f docker-compose.identity.yml up -d --force-recreate szejo-control-plane-mainframe
   ```
3. On boot, `_provision_from_manifests` re-reads `coder.yml`, sees
   `CODER_OIDC_CLIENT_SECRET` now set, and **overwrites** the stored hash
   (`if hashed_secret: client.client_secret = hashed_secret`) — no manual DB
   edit needed.
4. Confirm the value Coder's own container uses (`docker-compose.coder.yml`
   `CODER_OIDC_CLIENT_SECRET`) is the *same* plaintext — both containers read
   the same `${CODER_OIDC_CLIENT_SECRET}` from the same decrypted env file, so
   this should already match once step 1 is satisfied.
5. Retry Coder OIDC login. Verify in Mainframe logs:
   `[provision] Updated OAuth client: coder` (no "client secret will be unset"
   warning), then a clean `/authorize` → `/token` round trip.

## Other items checked, ruled out
- `require_pkce: false` in `coder.yml` — matches `main.py`'s default for
  confidential clients (`client_type == "public"`), not a bug.
- `redirect_uris` in `coder.yml` (`https://coder.sz3yan.com/api/v2/users/oidc/callback`)
  matches Coder's standard OIDC callback path — correct.
- Discovery document advertises `client_secret_post` and `none` — compatible
  with how Coder authenticates confidential clients.
- `CODER_OIDC_ISSUER_URL=${MAINFRAME_ISSUER_URL}` in `docker-compose.coder.yml`
  — correct, points Coder's discovery fetch at Mainframe.

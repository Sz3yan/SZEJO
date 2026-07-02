# Runbook — AAL3 client-certificate auth (Phase 5)

Strong (AAL3) login with an Aegis-issued client certificate, proven over a
dedicated Traefik **mTLS gateway** (`:8443`). Most-secure option: the cert is
verified in the TLS handshake against the control-plane CA; no third party in the
trust path.

Design: [`../architecture/aegis-cert-manager.md`](../architecture/aegis-cert-manager.md).
Code: mainframe `app/api/v1/cert_auth.py` + `app/services/cert_auth_service.py`.

## Why it is safe by default

The mainframe `/auth/cert` route is **dormant** until `MTLS_GATEWAY_SECRET` is set
AND the mTLS router stamps `X-Mtls-Verified`. On the public (Cloudflare) path that
header is never set, so a stolen — certs are public — cert PEM injected as a
header is rejected (`403`, fail closed). Channel binding comes from the real mTLS
handshake on `:8443`.

## One-time enable

```bash
# 1. Secrets.
szejo secrets rotate MTLS_GATEWAY_SECRET
szejo secrets set MAINFRAME_AEGIS_TOKEN   # mainframe token, scope aegis:manage (optional, for /enroll)

# 2. Aegis trust chain → both Traefik and mainframe.
curl -fsS https://aegis.sz3yan.com/ca/chain.pem -o manifest/traefik/certs/aegis-chain.pem
docker cp manifest/traefik/certs/aegis-chain.pem \
  szejo-control-plane-mainframe:/app/keys/aegis-chain.pem

# 3. Activate the Traefik mTLS dynamic config + insert the marker secret.
cp manifest/traefik/dynamic/mtls.yml.example manifest/traefik/dynamic/mtls.yml
sed -i "s#REPLACE_WITH_MTLS_GATEWAY_SECRET#$(szejo secrets get MTLS_GATEWAY_SECRET)#" \
  manifest/traefik/dynamic/mtls.yml
```

### 4. Add the `:8443` entrypoint + port (Traefik `command:` + `ports:`)

```yaml
# szejo-control-plane-proxy command:
- --entrypoints.websecure-mtls.address=:8443
# ports:
- "8443:8443"
```

### 5. Route `/auth/cert` over the mTLS entrypoint only (mainframe labels)

```yaml
- "traefik.http.routers.mainframe-mtls.rule=Host(`${MAINFRAME_HOST}`) && PathPrefix(`/auth/cert`)"
- "traefik.http.routers.mainframe-mtls.entrypoints=websecure-mtls"
- "traefik.http.routers.mainframe-mtls.tls=true"
- "traefik.http.routers.mainframe-mtls.tls.options=aegis-mtls@file"
- "traefik.http.routers.mainframe-mtls.middlewares=mtls-passcert@file,mtls-marker@file,crowdsec-bouncer@file"
- "traefik.http.routers.mainframe-mtls.service=mainframe-api"
```

```bash
szejo secrets run -- docker compose up -d szejo-control-plane-proxy szejo-control-plane-mainframe
```

## Enroll a user + log in

```bash
# Enroll (authenticated user) → returns cert + key ONCE; install in the browser/keystore.
curl -X POST https://mainframe.sz3yan.com/auth/cert/enroll -H "Authorization: Bearer $SESSION"
# Log in over the mTLS gateway with that client cert:
curl --cert client.crt --key client.key https://mainframe.sz3yan.com:8443/auth/cert
#   → session with acr=3 (AAL3), amr=[cert,hwk]
```

## Verify / revert

- **Negative:** POST `/auth/cert` on the public `:443` (no marker) → `403`.
- **Revoke a cert:** `POST https://aegis.sz3yan.com/v1/certificates/<serial>/revoke`
  (CRL-based rejection in mainframe is a follow-up; revocation is recorded now).
- **Revert:** `rm manifest/traefik/dynamic/mtls.yml` and drop the `:8443`
  entrypoint/labels. `/auth/cert` returns to dormant (403).

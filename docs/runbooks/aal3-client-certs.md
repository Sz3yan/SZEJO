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

The `:8443` entrypoint, published port, and the `mainframe-mtls` router labels
are committed in `docker-compose.edge.yml` / `docker-compose.identity.yml`.
The host-local, secret-bearing parts are two commands:

```bash
szejo tokens mint          # MAINFRAME_AEGIS_TOKEN (aegis:manage) for /enroll — see docs
szejo aegis enable-mtls    # MTLS_GATEWAY_SECRET + Aegis chain (Traefik + mainframe)
                           # + mtls.yml from the example + recreate proxy/mainframe
```

`enable-mtls` fetches `/ca/chain.pem` from Aegis into
`manifest/traefik/certs/aegis-chain.pem`, copies it to
`szejo-control-plane-mainframe:/app/keys/aegis-chain.pem`, renders
`manifest/traefik/dynamic/mtls.yml` (gitignored — carries the marker secret),
and recreates the proxy + mainframe. Re-run any time (idempotent) — e.g. after
the Aegis CA is rotated.

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

# Runbook — internal TLS via Aegis (Phase 4)

Serve Traefik's `:443` with a certificate issued by the control-plane CA (Aegis)
instead of Traefik's auto-generated self-signed default. **Opt-in.**

Background: [`../architecture/aegis-cert-manager.md`](../architecture/aegis-cert-manager.md).

## Scope / blast radius

Public traffic terminates TLS at **Cloudflare** (`cloudflared → Traefik :80`), so
this affects **only direct `:443` access** (host / Tailscale / internal). It is
additive — the Aegis cert is presented for `*.sz3yan.com` SNIs; Traefik's default
cert still serves everything else. Reverting = delete one file.

## Prerequisites

- Aegis running with its CA initialized (`POST /admin/ca/init` succeeded).
- A mainframe token with scope `aegis:manage` — `szejo certs` reuses
  `MAINFRAME_AEGIS_TOKEN` from the pass store (same service token the
  mainframe container consumes as `AEGIS_MGMT_TOKEN`); set `AEGIS_MGMT_TOKEN`
  only to override it.

## Enable

```bash
# 1. Issue the cert (Aegis generates the keypair, returns it once).
#    No wrapper — szejo subcommands self-inject their declared keys.
szejo certs issue --cn sz3yan.com --sans '*.sz3yan.com,sz3yan.com'
#    → writes manifest/traefik/certs/internal.{cert,key}.pem

# 2. Activate the dynamic config (Traefik hot-reloads via file.watch).
cp manifest/traefik/dynamic/tls-internal.yml.example \
   manifest/traefik/dynamic/tls-internal.yml
```

## Verify

```bash
# Present the cert on a direct :443 connection and check the issuer chain.
echo | openssl s_client -connect <host>:443 -servername mainframe.sz3yan.com 2>/dev/null \
  | openssl x509 -noout -issuer -subject
# Trust the Aegis root for clients that validate:
curl -fsS https://aegis.sz3yan.com/ca/root.pem -o szejo-root.pem
```

## Renew (90-day leaves)

```cron
0 3 1 * * cd /path/to/szejo-control-plane && \
  szejo certs renew
```
Re-issues + overwrites the files; Traefik hot-reloads on the change.

## Revert

```bash
rm manifest/traefik/dynamic/tls-internal.yml   # back to the default cert
```

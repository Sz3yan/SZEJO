# Aegis — Certificate Manager (private CA)

Aegis is the control plane's private CA + certificate lifecycle. Built custom
(FastAPI + Postgres), mirroring Janus/Mainframe. Named for the shield of trust.

**Aegis holds no private keys.** The CA private keys live in **Janus** (the KMS).
To issue a certificate Aegis builds the TBSCertificate (via `asn1crypto`), sends
the DER bytes to Janus to sign, then assembles the final certificate with that
signature. This is what keeps the CA keys in the KMS.

## CA hierarchy

```
Root CA  (ca-root key in Janus, ES384, self-signed, CA pathlen:1)
   └── TLS CA  (ca-tls key in Janus, ES384, CA pathlen:0)
          └── leaf certs (server / client), CRL
```

Both CA keys are ECDSA P-384 → every certificate + CRL is `sha384_ecdsa`. A
future `client-auth` intermediate backs AAL3 client certs (Phase 5).

## How issuance works

1. Caller `POST /v1/certificates` with a CSR (Aegis uses its public key + CN/SAN)
   or just `subject_cn`/`dns_names` (Aegis generates the keypair, returns the
   private key **once**, never stores it).
2. Aegis builds the leaf TBS (issuer = TLS CA, SAN, EKU, CRL distribution point).
3. Aegis calls Janus `POST /v1/keys/ca-tls/sign` over the TBS DER → DER signature.
4. Aegis assembles the certificate, stores its metadata (serial, subject, PEM,
   status), and returns the cert + full chain (leaf → TLS CA → root).

`app/core/x509_builder.py` builds the ASN.1; `app/core/signer.py` is the
Signer abstraction (`JanusSigner` in prod, `LocalSigner` for tests/bootstrap).

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/ca/root.pem` | public | Root CA cert (trust anchor) |
| `GET` | `/ca/chain.pem` | public | Intermediate + root |
| `GET` | `/crl` | public | Current CRL (`application/pkix-crl`) |
| `POST` | `/admin/ca/init` | mainframe `aegis:manage` | Idempotently build Root + TLS CAs |
| `POST` | `/v1/certificates` | mainframe `aegis:manage` | Issue a leaf (server/client) |
| `GET` | `/v1/certificates` | mainframe `aegis:manage` | List issued certs |
| `POST` | `/v1/certificates/{serial}/revoke` | mainframe `aegis:manage` | Revoke |
| `GET` | `/health` | public | Liveness |

`/ca/*` and `/crl` are intentionally public (relying parties must fetch trust
anchors + revocation). Management endpoints require a mainframe-issued token;
**no** Traefik forward-auth is applied (it would block public trust distribution).

## Storage

Postgres (`szejo-control-plane-aegis-db`): `cas` (tier, Janus key name, subject,
SKI, cert PEM, CRL counter) and `certificates` (serial, subject, profile,
validity, PEM, status, revoked_at). **No private keys** — CA keys in Janus;
leaf private keys returned to the caller and discarded.

## Bootstrap + deploy

- Needs `AEGIS_JANUS_TOKEN` (mainframe service token, scope `janus:manage`) to
  mint CA keys + sign in Janus. CA bootstrap runs on startup; if Janus isn't
  reachable it's deferred — re-run with `POST /admin/ca/init`.
- Image `ghcr.io/sz3yan/aegis`, built by `.github/workflows/aegis.yml`. Boots →
  `alembic upgrade head` → uvicorn → CA bootstrap.
- Secrets: `AEGIS_POSTGRES_PASSWORD` (szejo secrets), `AEGIS_JANUS_TOKEN` (manual).

## Consumers

- **Internal TLS (Phase 4 — tooling shipped, opt-in):** issue a Traefik serving
  cert from Aegis with `szejo certs issue`, activate via
  `manifest/traefik/dynamic/tls-internal.yml`, renew with
  `szejo certs renew`. Only affects direct `:443` (public TLS is
  Cloudflare's). See [`../runbooks/internal-tls.md`](../runbooks/internal-tls.md).
- **AAL3 client certs (Phase 5 — shipped):** Mainframe `/auth/cert/enroll`
  issues a client cert via Aegis; `/auth/cert` validates it (chain to Aegis root
  + clientAuth EKU + validity) over a Traefik mTLS gateway and issues an `acr=3`
  session. Trusted only via the verified mTLS channel (gateway-secret marker).
  Setup: [`../runbooks/aal3-client-certs.md`](../runbooks/aal3-client-certs.md).
  Follow-up: CRL enforcement at validation time (revocation is recorded now).

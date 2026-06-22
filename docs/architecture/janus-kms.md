# Janus — Key Management System (KMS)

Janus is the control plane's single custodian of private key material. Other
services never store raw private keys; they ask Janus to *operate* (sign,
encrypt, decrypt). Built custom (FastAPI + Postgres), mirroring Mainframe.

Named for the Roman god of keys, doorways, and transitions.

## The seal (the critical primitive)

Every private key is stored only as **ciphertext wrapped by a master key (KEK)**:

- The KEK is derived at boot from `JANUS_UNSEAL_KEY` (a high-entropy secret in
  .env.enc, managed via szejo secrets) via **HKDF-SHA256**, and lives **only in memory** — never
  persisted.
- Key material is wrapped with **AES-256-GCM** (`nonce ‖ ciphertext+tag`).
- If `JANUS_UNSEAL_KEY` is absent, Janus boots **sealed**: `/health` stays green
  but every crypto endpoint returns `503`. A *wrong* unseal key fails closed —
  GCM authentication fails on first unwrap, so a mismatched KEK can never
  silently "work".

`JANUS_UNSEAL_KEY` is the crown jewel: its compromise exposes every key Janus
holds. Back it up offline; rotating it re-seals (re-wraps) all material and is a
key-migration event, not a routine rotation. (Roadmap: Shamir-split the unseal
key; HSM-back the KEK.)

Code: `app/core/seal.py`.

## Key model

A `Key` is a named, versioned handle (e.g. `img/mainframe`); its material lives
in `KeyVersion` rows so rotation never breaks references.

- **Signing** uses the primary version; **verification** may use any version.
- **Rotation** appends a new version and bumps `primary_version` (old signatures
  still verify against their pinned version).
- **Destroy** marks the key + versions unusable (refuses further ops).

The database stores only `wrapped_private` (sealed bytes) + the public PEM —
**plaintext private bytes never touch a column.** Code: `app/models/key.py`.

### Supported key types

| Type | Algorithm | Operations |
|---|---|---|
| `ES256` / `ES384` | ECDSA P-256 / P-384 | sign/verify (`ES256` is the cosign default) |
| `ED25519` | Ed25519 | sign/verify |
| `RSA2048/3072/4096` | RSA-PSS (sign), RSA-OAEP (encrypt) | sign/verify, encrypt/decrypt |
| `AES256` | AES-256-GCM | encrypt/decrypt |

We only **compose** vetted `cryptography` primitives — no hand-rolled algorithms.
Code: `app/core/crypto.py`.

## API (v1)

All endpoints require the management scope (`janus:manage`) on a Mainframe-issued
OIDC token and require Janus to be unsealed. Private material is **never**
returned — only public keys, signatures, ciphertext.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/keys` | create a key |
| `GET` | `/v1/keys` | list keys |
| `GET` | `/v1/keys/{name}/public` | public key (all versions) |
| `POST` | `/v1/keys/{name}/sign` | sign a message/digest (`prehashed` for cosign) |
| `POST` | `/v1/keys/{name}/verify` | verify a signature |
| `POST` | `/v1/keys/{name}/encrypt` | encrypt (AES/RSA keys) |
| `POST` | `/v1/keys/{name}/decrypt` | decrypt |
| `POST` | `/v1/keys/{name}/rotate` | add a new version, bump primary |
| `DELETE` | `/v1/keys/{name}` | destroy a key |
| `GET` | `/health`, `/seal/status` | liveness + sealed/unsealed (no auth) |

Binary fields (message/signature/ciphertext/plaintext) are base64url on the wire.
The `prehashed` flag makes EC keys sign a precomputed SHA-256 digest and return a
DER ECDSA signature — exactly the shape cosign needs (Phase 2).

## Audit

Every operation is recorded to an append-only log (`audit_events`): actor
(Mainframe `sub` or, later, GitHub repo/workflow), action, key name, result,
request id. No key material, plaintext, or signature bytes are ever logged.

## Exposure

The **management API** (`/v1/keys`, `/seal`) is internal-only — no Traefik route;
reachable on `orchubi_network` by container hostname (`szejo-control-plane-janus`).

A **narrow public surface** is routed at `janus.sz3yan.com`: only `/v1/transit/*`
(a HashiCorp-Vault-Transit-compatible signing API) and `/health`. cosign's
built-in `hashivault` provider uses it to sign/verify CI images with no custom
plugin, gated by GitHub Actions OIDC + per-key policy. See
[`image-signing.md`](image-signing.md). The management API is **not** matched by
the public router, so key creation/rotation/decrypt stay internal.

## Deploy

- Image `ghcr.io/sz3yan/janus`, built by `.github/workflows/janus.yml`.
- Boots → `alembic upgrade head` → uvicorn → unseal from `JANUS_UNSEAL_KEY`.
- Secrets: `JANUS_UNSEAL_KEY`, `JANUS_POSTGRES_PASSWORD` (both via szejo secrets).

## Runbook (operate)

```bash
# Status (is it sealed?)
python3 -m scripts.szejo secrets run -- \
  docker compose exec szejo-control-plane-janus \
  python -c "import urllib.request,json; print(urllib.request.urlopen('http://localhost:8000/seal/status').read())"

# Rotate the DB password (also needs ALTER USER in the db container)
python3 -m scripts.szejo secrets rotate JANUS_POSTGRES_PASSWORD --restart

# Rotate the unseal key — CAUTION: re-seals every key (migration event), plan it.
```

# SZEJO Control Plane — Platform Pillars

The control plane is not just a host for services; it is a **platform** that
provides four foundational capabilities every szejo-service consumes instead of
reinventing:

| Pillar | Service | Status | Responsibility |
|---|---|---|---|
| **Identity** | Mainframe | live | Who you are — OAuth 2.1 / OIDC, passkeys, JWTs, forward-auth |
| **Keys** | **Janus** | Phase 1 | Cryptographic key custody + operations (sign/encrypt) |
| **Certificates** | **Aegis** | Phase 3 (MVP) | Private CA — issue/revoke/CRL; internal TLS + AAL3 certs later |
| **Provisioning** | Coder / Terraform / Compose | live | Standing services + infra up |

## Target architecture

```
                         szejo-control-plane
  ┌──────────────┐   OIDC    ┌───────────────────────────────────────┐
  │  mainframe   │◀─────────▶│  identity (who you are)        [live]  │
  │   (IdP)      │           └───────────────────────────────────────┘
  └──────┬───────┘
         │ gates issuance (client_credentials / AAL3 login)
         ▼
  ┌──────────────┐  sign(TBSCert), wrap/unwrap   ┌──────────────────┐
  │    Aegis     │──────────────────────────────▶│      Janus       │
  │ Cert Manager │  CA private keys NEVER leave  │      (KMS)       │
  │  (Phase 3)   │  Janus; Aegis sends bytes,    │  key custody     │
  │              │  Janus returns signature      │  sign / encrypt  │
  └──────┬───────┘                               └────────┬─────────┘
         │ internal TLS, client certs (AAL3)              │ sign(image digest)
         ▼                                                ▼
   Traefik / mainframe /auth/cert            cosign --key janus://… (all CI builds)
```

## How services consume the pillars

- **Identity** — services authenticate users/each other via Mainframe OIDC
  (`client_credentials` for service-to-service, authorization-code for users).
- **Keys** — instead of holding their own private keys, services ask Janus to
  *operate* (sign a token, sign an image digest, encrypt a blob). The private key
  never leaves Janus. Image signing for **every** CI build uses a per-service
  Janus key.
- **Certificates** (planned) — Aegis issues X.509 (internal TLS for Traefik,
  AAL3 client certs gated by Mainframe). Aegis never holds CA private keys —
  it builds the certificate and Janus signs it.

## Trust + bootstrap (the sealing chain)

There is a deliberate bottom turtle so trust is anchored, not circular:

```
szejo secrets (pass store, GPG key)       ← the root secret store
   └─ JANUS_UNSEAL_KEY  ──► Janus unseals (derives in-memory KEK)
        └─ Janus holds CA keys ──► Aegis initializes its CA via Janus
             └─ Aegis issues certs ──► Traefik internal TLS, client certs
```

- Janus boots **sealed** and refuses all crypto until it receives
  `JANUS_UNSEAL_KEY` (from szejo secrets). The master key (KEK) exists only in memory.
- Compose `depends_on` encodes the order: `janus-db → janus → (aegis-db → aegis)`.
- szejo secrets remains the root-of-trust until/unless Janus's KV engine supersedes it
  (a later, optional phase).

## Exposure model

- **Janus management is internal-only** (no Traefik route; reachable on
  `orchubi_network` by container hostname). A *narrow*, GitHub-OIDC-gated public
  surface at `janus.sz3yan.com` exposes only `/v1/transit/*` (cosign signing) +
  `/health`, so CI can sign images without reaching the management API.
- **Aegis** (planned) gets a minimal public surface (`aegis.sz3yan.com`) for
  client-cert enrollment + serving the root cert / CRL, gated by Mainframe
  forward-auth.

See [`janus-kms.md`](janus-kms.md) for the Janus design, and
[`../decisions/0001-custom-kms-and-cert-manager.md`](../decisions/0001-custom-kms-and-cert-manager.md)
for why these are custom-built.

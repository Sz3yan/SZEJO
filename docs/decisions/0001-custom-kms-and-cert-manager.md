# ADR 0001 — Custom KMS (Janus) + Cert Manager (Aegis), and KMS-key image signing

- **Status:** accepted
- **Date:** 2026-06-15
- **Supersedes:** the standalone self-hosted Fulcio CA deploy-gate proposal

## Context

The control plane needed (1) a trust root for image-signing that lives in the
control plane (not Sigstore's public Fulcio), and (2) a path toward AAL3 client
certs. The first cut was a one-off Fulcio CA + a host verified-updater. Rather
than ship a narrow one-off, we reframed it as two reusable platform services that
serve *all* key + certificate needs: **Janus** (KMS) and **Aegis** (Cert
Manager). See [`../architecture/platform-pillars.md`](../architecture/platform-pillars.md).

Three decisions needed recording.

## Decision 1 — Build custom, not adopt OpenBao/step-ca

We considered adopting OpenBao (Vault fork) for the KMS and step-ca for the CA.
We chose to **build custom FastAPI services**, consistent with Mainframe being a
from-scratch OAuth 2.1/OIDC implementation.

- **Why:** full ownership of the data model, API shape, and operational fit to
  szejo conventions (szejo secrets, compose, Traefik, the audit/Merkle pattern); no
  external operational surface or licensing concerns; the team already operates a
  custom security-critical service (Mainframe).
- **Cost (accepted):** we own the most security-critical code in the platform.
- **Mitigations:** never hand-roll crypto — only compose vetted `cryptography`
  primitives (AES-GCM, ECDSA/EdDSA, RSA-PSS/OAEP, X.509 builder); a tight,
  audited API that never exports private key material; envelope encryption with
  an in-memory KEK; full audit log.

## Decision 2 — Names: Janus (KMS) and Aegis (Cert Manager)

Fits the existing pantheon (Mainframe, Hermes, Atlas). **Janus** — Roman god of
keys and doorways — for the key store. **Aegis** — the shield of trust — for the
certificate authority.

## Decision 3 — Image signing with a Janus-held key (drop Fulcio)

`cosign` signs with a per-service key **held in Janus** (`janus://keys/img/<svc>`),
not via a keyless Fulcio CA.

- **Why:** the private key never leaves the KMS; no public CA endpoint to operate
  or harden; fewer moving parts; reuses the KMS we are building anyway. Per-service
  keys bound the blast radius (a compromised CI for one service cannot forge
  another's image).
- **Trade-off:** loses cosign "keyless" (ephemeral-cert) ergonomics; a small
  `sigstore-kms-janus` cosign plugin is required to bridge cosign ↔ Janus.
- **Reachability:** CI runs on public GitHub, so Janus exposes a *narrow*,
  GitHub-OIDC-gated `/sign` surface (the rest of the API stays internal).

### Implementation note (Phase 2) — Vault-Transit-compatible surface, not a custom plugin

The plan called for a custom `sigstore-kms-janus` cosign plugin and a `janus://`
key scheme. Implementation chose a **HashiCorp-Vault-Transit-compatible surface**
on Janus (`/v1/transit/*`) so cosign's **built-in `hashivault` provider** signs
and verifies against Janus with **no custom plugin** — `cosign sign --key
hashivault://img-<svc>` with `VAULT_ADDR=https://janus.sz3yan.com`.

- **Why:** no separate Go binary to build/distribute/version against cosign; reuses
  cosign's battle-tested provider; the wire format is unit-testable in Python
  (`janus/tests/test_transit.py` proves the DER-over-digest signature verifies
  with the published public key). It is interop with a known format, **not** an
  adoption of Vault — Janus remains the custom KMS and the sole key custodian.
- **Cost:** the operator-visible key URL says `hashivault://`; a future
  `sigstore-kms-janus` plugin could restore the `janus://` scheme if desired.

## Consequences

- The standalone Fulcio compose service, `manifest/sigstore/`, and the
  `FULCIO_HOST`/`CA_ROOT_*` settings were removed (Phase 0).
- `szejo certs` is retained (now a thin Aegis/Janus HTTP client — no local CA
  material). `szejo deploy verify` and the cosign deploy-verify gate are
  **parked** under `future-implementation/` — szejo isn't stable enough yet
  for an unattended deploy gate to be worth the operational risk.
- Watchtower has been **removed entirely**, both from the control plane and
  from every Coder workspace template. There is no auto-updater anywhere
  right now: control-plane image updates are manual
  (`docker compose pull <service> && up -d <service>`); Coder workspaces
  (portfolio/atlas/sentinel) pull their latest image on every workspace
  restart instead (their startup scripts run `docker compose pull` before
  `up -d`).

# Image signing + verified deploys (via Janus KMS)

**Every** container image built in GitHub Actions is signed with a **per-service
key held in Janus** (the control-plane KMS); the deploy host refuses to run any
image it cannot verify. The private signing key never leaves Janus.

This supersedes the earlier self-hosted-Fulcio approach (see
[ADR 0001](../decisions/0001-custom-kms-and-cert-manager.md)).

## How it works (no custom cosign plugin)

cosign has a built-in `hashivault` KMS provider. Janus exposes a **small
HashiCorp-Vault-Transit-compatible surface** (`/v1/transit/*`), so cosign signs
and verifies against Janus with **zero custom plugin** — interop with a
well-known wire format, not an adoption of Vault.

```
CI (per image build)                         Janus (KMS)                deploy host
──────────────────                           ───────────                ───────────
build+push image@sha256                       img-<svc> key (sealed)
GitHub OIDC token (aud=janus) ──X-Vault-Token─▶ /v1/transit/sign        keys/<svc>.pub (pinned once)
cosign sign --key hashivault://img-<svc>  ◀── vault:vN:<DER sig>
                                                                          cosign verify --key <svc>.pub
                                                                          digest new + verified → compose up
```

- **Per-service keys** (`img-mainframe`, `img-workspace`, `img-janus`,
  `img-aegis`) — a compromised CI for one service cannot forge another's image.
- **Auth = GitHub Actions OIDC.** The `sign-image` action mints an OIDC token
  with `audience=janus`; Janus validates the signature, issuer, audience, then
  enforces the token's `repository` / `workflow_ref` against the key's policy
  (fail-closed: a CI key must declare a policy).
- **Exposure.** Only `/v1/transit/*` + `/health` are routed publicly at
  `janus.sz3yan.com`; the management API (`/v1/keys`) stays internal.

## CI side — the reusable action

`.github/actions/sign-image` wraps cosign so every workflow signs identically:

```yaml
- name: Sign <svc> image (Janus KMS)
  uses: ./.github/actions/sign-image
  with:
    image: ghcr.io/sz3yan/<svc>@${{ steps.build.outputs.digest }}
    key: img-<svc>
```

Wired into `mainframe.yml`, `workspace-image.yml`, `janus.yml` (and `aegis.yml`
when it lands). The calling job needs `permissions: id-token: write`.

`img-mainframe`, `img-janus`, `img-sentinel` are provisioned and verified live
(real `cosign sign` against the running Janus, confirmed by a Rekor tlog entry
for each). The sign steps no longer carry `continue-on-error` — a signing
failure now fails the build, on top of the host gate refusing unverifiable
images. `img-aegis`/`img-workspace` still need the same one-time setup below
before their workflows can drop it too.

## One-time setup (host / admin)

1. **Create the keys** (internal management API, mainframe `janus:manage` token):
   ```bash
   curl -XPOST http://szejo-control-plane-janus:8000/v1/keys \
     -H "Authorization: Bearer $MF_TOKEN" -H 'content-type: application/json' \
     -d '{"name":"img-mainframe","key_type":"ES256",
          "policy":{"github_repository":"sz3yan/szejo-control-plane",
                    "github_workflow_ref_prefix":"sz3yan/szejo-control-plane/.github/workflows/mainframe.yml@"}}'
   # repeat for img-workspace, img-janus, img-aegis (matching each workflow_ref)
   ```
2. **Pin each public key** for the host gate (decouples deploy from Janus uptime):
   ```bash
   VAULT_ADDR=https://janus.sz3yan.com VAULT_TOKEN=$MF_TOKEN \
     cosign public-key --key hashivault://img-mainframe > keys/img-mainframe.pub
   ```
3. **Enable the gate** for a service (replaces Watchtower for it):
   ```bash
   python3 -m scripts.szejo secrets run -- python3 -m scripts.szejo deploy verify \
     szejo-control-plane-mainframe ghcr.io/sz3yan/mainframe keys/img-mainframe.pub
   # then schedule via cron (*/2) in ~/bin/szejo-infra-startup.sh and drop the
   # com.centurylinklabs.watchtower.enable label on that service.
   ```

## Verification / negative test

- **Positive:** push → CI signs via Janus → host `szejo deploy verify` verifies
  with the pinned key → `compose up`. `/health` green.
- **Negative:** point the gate at an unsigned/foreign image (or tamper the
  digest) → `cosign verify` exits non-zero → updater refuses → image never runs.
- **Authz:** a token from another repo/workflow → Janus `/v1/transit/sign`
  returns 403 (policy mismatch), proven by `janus/tests/test_transit.py`.

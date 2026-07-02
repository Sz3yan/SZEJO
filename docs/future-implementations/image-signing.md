# Image signing: cosign + Janus's Vault-Transit-compatible surface

## Why this exists

Container images built in CI need to be signed so that anything pulling them
(deploy pipeline, runtime admission check) can prove the artifact actually
came from our build and wasn't swapped in the registry. The standard tool for
this is [sigstore/cosign](https://github.com/sigstore/cosign).

cosign supports several KMS backends for the signing key (AWS KMS, GCP KMS,
Azure Key Vault, HashiCorp Vault Transit, ...). We don't want to depend on a
third-party KMS or run a real HashiCorp Vault — we already run Janus, our own
key-management service, as part of the control plane. So instead of adopting
Vault, Janus implements the *subset* of Vault's Transit HTTP API that cosign's
built-in `hashivault` provider talks. cosign believes it's talking to Vault;
it's actually talking to Janus.

**There is no HashiCorp Vault anywhere in this stack.** The word "vault" here
refers only to the wire protocol shape cosign expects, not the product.

The benefit: the signing private key never leaves Janus, never touches a CI
runner or a file on disk, and every signature requires Janus to authenticate
and authorize the caller per-key. Compromising a CI job can't exfiltrate a
signing key, because there is no key material to exfiltrate — only short-lived
authority to ask Janus to sign one digest.

## Why the Vault-shaped names can't be renamed

cosign's `hashivault://` provider is built on HashiCorp's own Vault Go client
library. That library is what actually issues the HTTP calls — cosign just
selects it based on the `hashivault://` URI scheme. The client library:

- reads connection config from the env vars `VAULT_ADDR` and `VAULT_TOKEN`
  (hardcoded names, not configurable)
- sends the token in the header `X-Vault-Token` (Janus's
  `get_transit_principal` dependency reads this exact header — see
  `app/dependencies.py`)
- calls fixed REST paths: `GET /v1/transit/keys/{name}`,
  `POST /v1/transit/sign/{name}[/{hash_algorithm}]`,
  `POST /v1/transit/verify/{name}[/{hash_algorithm}]`
- expects a fixed response envelope: `{"data": {...}}`
- expects the signature format `vault:vN:<base64 DER>`

None of this is something Janus or this repo controls — it's baked into the
Vault client library cosign vendors. Renaming `/v1/transit/...`, `VAULT_ADDR`,
`VAULT_TOKEN`, or `X-Vault-Token` would simply break compatibility; cosign
would 404 or never send the request. This is why `app/api/v1/transit.py`
explicitly calls itself "interop with a well-known wire format, NOT an
adoption of Vault" — the Vault-flavored names are unavoidable surface area,
not a design choice we made.

**What we already do rename**, at the boundary the *caller* sees, so the
"Vault" framing doesn't leak into our own configuration:

- The GitHub Action (`.github/actions/sign-image/action.yml`) exposes
  `janus-url` and `oidc-audience` as its public inputs — not `vault-addr` /
  `audience`. Those get mapped internally to `VAULT_ADDR` / `AUDIENCE` only at
  the point where they're handed to `cosign`.
- Key names are Janus's own naming (`img-mainframe`, `img-janus`, ...), not
  Vault path conventions.

Anything *internal* to `transit.py` that isn't part of the wire contract
(docstrings, the router's `tags=["transit"]`, variable names, the module name
itself) is free to be renamed for clarity without touching compatibility. The
constraint is specifically: any literal that appears in an HTTP request line,
header name, env var name, or JSON envelope key must stay exactly as Vault's
client library expects it.

## The cast

- **Janus** — our KMS. Holds private keys, sealed at rest, exposes
  `/v1/transit/*` (this doc) plus a management API for key lifecycle
  (create/rotate/destroy), gated by mainframe-issued tokens
  (`require_management` in `app/dependencies.py`).
- **cosign** — sigstore's signing CLI, invoked from CI. Its `hashivault`
  provider is the only piece that thinks it's talking to Vault.
- **GitHub Actions OIDC** — proves *which* workflow/repo is asking Janus to
  sign, without any long-lived secret stored in GitHub.
- **The registry** — stores the image and the cosign signature as a
  sibling OCI artifact (not a separate file you have to ship around).

`janus.sz3yan.com` is the real, live hostname cosign talks to — it's the
default for the action's `janus-url` input
(`.github/actions/sign-image/action.yml`), used as `VAULT_ADDR` for the
`cosign sign` invocation.

## End-to-end flow: signing an image in CI

1. **CI builds the image** and gets back an immutable digest
   (`repo@sha256:...`). Signing the digest, not a mutable tag, is what makes
   the signature meaningful — a tag can be repointed after signing.

2. **The job mints a GitHub Actions OIDC token** scoped to this signing step:

   ```bash
   VAULT_TOKEN="$(curl -sSf \
     -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
     "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${AUDIENCE}" | jq -r '.value')"
   ```

   `ACTIONS_ID_TOKEN_REQUEST_TOKEN` / `_URL` are ambient GitHub-provided
   credentials available only to a job with `permissions: id-token: write`.
   The resulting JWT is signed by GitHub, scoped to this specific
   repo/workflow/ref, short-lived, and carries an `aud` claim equal to
   `janus` (must match what Janus expects, see `oidc-audience` input).
   This token is reused as the "Vault token" — Janus doesn't issue its own
   CI credentials; it trusts GitHub's.

3. **cosign is invoked**:

   ```bash
   VAULT_ADDR=https://janus.sz3yan.com VAULT_TOKEN=<the OIDC JWT> \
     cosign sign --yes --key "hashivault://img-mainframe" <image-digest>
   ```

   cosign's Vault client first calls `GET /v1/transit/keys/img-mainframe`
   to fetch the public key (used later to build the verifier / bundle), then
   `POST /v1/transit/sign/img-mainframe` with the digest.

4. **Janus authenticates the caller** (`get_transit_principal`, in
   `app/dependencies.py`):
   - Reads the JWT from the `X-Vault-Token` header.
   - Peeks at the unverified `iss` claim to decide which issuer to validate
     against: GitHub Actions OIDC, or Janus's own mainframe issuer (used for
     host/admin signing, not CI).
   - For the GitHub case, fully verifies the JWT signature against GitHub's
     published JWKS, checks `iss` and `aud` (`validate_github_token` in
     `app/core/auth.py`). The result is a `Principal` whose `subject` is the
     source repository and whose `claims` carry the full token (including
     `repository`, `workflow_ref`, etc).

5. **Janus authorizes the caller against this specific key's policy**
   (`_authorize` in `app/api/v1/transit.py`):
   - Loads `Key.policy` for `img-mainframe` — e.g.
     `{"github_repository": "org/repo", "github_workflow_ref_prefix": "org/repo/.github/workflows/build.yml@"}`.
   - A CI-type principal with no matching policy on the key is rejected
     (`403 key has no CI policy`) — fail closed, a key is never CI-signable
     by default.
   - If the policy specifies a repo, the token's `repository` claim must
     match exactly. If it specifies a workflow-ref prefix, the token's
     `workflow_ref` claim must start with it. This is what stops repo A's
     CI from signing repo B's image, even though both can reach Janus.

6. **Janus checks it's unsealed** (`require_unsealed`). If the KEK hasn't
   been derived this boot (see "Sealing" below), every crypto endpoint
   returns `503` regardless of auth — there's nothing to sign with.

7. **Janus signs**, never exposing the private key:
   - `KeyService.sign()` loads the key's current `KeyVersion`, calls
     `seal.unwrap()` to decrypt the private key material into memory using
     the in-memory KEK, signs the digest, and the unwrapped plaintext key
     goes out of scope immediately after — it's never written anywhere, never
     returned to the caller, never logged.
   - The signature is wrapped in Vault's expected envelope:
     `{"data": {"signature": "vault:v3:<base64 DER signature>"}}`.
   - The operation is recorded in Janus's audit log
     (`AuditService.record(..., action="transit.sign", ...)`) — actor,
     key name, key version used, request id. This is the record of
     "who signed what, when," independent of anything in the registry.

8. **cosign attaches the signature to the image** in the registry as an OCI
   artifact alongside the image manifest — not a separate file to track or
   lose. The public key fetched in step 3 is what makes the resulting bundle
   self-describing for later verification.

## Verifying a signed image

This is fully offline — no call to Janus required:

```bash
cosign verify --key keys/img-mainframe.pub <image-digest>
```

`keys/img-mainframe.pub` (committed in the repo, see
`szejo-control-plane/keys/`) is the public half of the same key Janus signed
with — fetched once via step 3 above and checked into source control because
public keys carry no secrecy requirement. cosign hashes the image manifest,
verifies the attached signature against this public key with standard ECDSA
verification, and exits non-zero if it doesn't match or no signature is
found. Any deploy gate or admission controller can run this same check before
trusting an image.

If a key is rotated in Janus (`KeyService.rotate`), the **old public key
keeps working for old signatures** — `GET /v1/transit/keys/{name}` returns
*all* versions, keyed by version number, and the signature envelope
(`vault:vN:...`) records which version signed it. Verification by version,
not just "the current key," is why rotation doesn't retroactively invalidate
already-signed images.

## Sealing: why a 503 instead of always-on signing

Janus never holds private keys in plaintext outside of the brief window a
sign/verify operation needs them. At rest, every private key is encrypted
("wrapped") under a master key (KEK) using AES-256-GCM
(`app/core/seal.py`). The KEK itself is derived via HKDF-SHA256 from
`JANUS_UNSEAL_KEY`, a bootstrap secret stored encrypted in `.env.enc`
(see `.sops.yaml`) and supplied at process start.

The KEK is held **only in memory**, never persisted. If Janus restarts and
isn't given the unseal key again, it comes up *sealed*: the database still
has all the wrapped key material, but there's no KEK to unwrap it with, so
`require_unsealed` rejects every sign/verify call with `503`. This means a
stolen database backup is useless without also having the unseal key, and a
compromised-but-unsealed-yet Janus process can't be made to sign anything.

## Summary: what each "Vault-looking" name actually means here

| Vault-shaped name | Where it comes from | Renamable? |
|---|---|---|
| `hashivault://` scheme | cosign's provider registry | No — cosign-side constant |
| `VAULT_ADDR` / `VAULT_TOKEN` env vars | Vault's Go client lib (vendored by cosign) | No — client-side constant |
| `X-Vault-Token` header | Same client lib | No |
| `/v1/transit/keys\|sign\|verify/{name}` paths | Same client lib | No |
| `{"data": {...}}` envelope, `vault:vN:<sig>` format | Vault's wire format | No |
| `janus-url`, `oidc-audience` (action inputs) | This repo | Already renamed away from Vault terms |
| `img-mainframe`, `img-janus`, ... (key names) | This repo | Already Janus-native naming |
| `transit.py` filename, docstrings, comments | This repo | Yes, cosmetic only — no wire impact |

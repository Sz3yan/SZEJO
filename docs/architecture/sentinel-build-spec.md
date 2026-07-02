# Sentinel — Build Spec

> **Service name:** `sentinel` (renamed from the original `szejo-scan`, which itself replaced an earlier `aegis` placeholder — `aegis` is taken by the Certificate Manager in this repo). It appears in domains, image names, network names, and the GitHub App slug. Keep it consistent everywhere.

A self-hosted security scanning platform in the Aikido mould: a unified layer over open-source scanners that surfaces findings on every push/PR and (phase 2) ships one-click AI auto-fix PRs. Installs into a user's GitHub via a **GitHub App**. Third SZEJO service, alongside Atlas and Mainframe.

This document is written to be handed to a coding agent. It is opinionated, specifies exact APIs, and includes copy-paste scaffolding. Where a decision is load-bearing, the reason is stated so the agent does not "helpfully" undo it.

---

## 0. Non-negotiable decisions (do not deviate)

1. **Install mechanism is a GitHub App**, not OAuth, not a bot account, not a user-pasted PAT. The App is the productized "install into your repo" primitive and is what Aikido's "Add Repo" flow is under the hood.
2. **GitHub App JWTs are RS256.** This is GitHub's requirement — the App private key is RSA and GitHub-generated. This is *distinct from* Mainframe's ES256. Do **not** try to unify them. They authenticate different things (see §1).
3. **SARIF is the internal normalization format.** Every scanner's output is converted to SARIF 2.1.0 immediately. All storage, dedup, and check-run rendering operates on SARIF. Adding scanner #4–N later is then config, not a rearchitect.
4. **Scanning runs in an ephemeral sandbox**: mint token → clone → scan → collect SARIF → destroy. Nothing persists between scans except findings in Postgres.
5. **The webhook handler returns fast** (< 2s) and does zero scanning inline. GitHub times webhook deliveries out at ~10s. All work is enqueued to Redis and processed by workers.
6. **Auto-fix (phase 2) uses bring-your-own-key / local model.** The platform never embeds a vendor key it pays for. This is the structural reason Sentinel can offer unlimited auto-fix where Aikido meters it to 2/month free.

---

## 1. Two auth flows — keep them separate

This is the single most common place this kind of system gets confused. There are two completely independent auth directions:

| Flow | Direction | Mechanism | Lives where |
|---|---|---|---|
| **User → Platform** | a human logs into Sentinel | Mainframe OIDC (ES256, PKCE, WebAuthn) at `mainframe.sz3yan.com` | existing Mainframe IdP |
| **Platform → GitHub** | Sentinel reads code / opens PRs | GitHub App (RS256 JWT → installation token) | this build |

A Mainframe **tenant** owns zero or more GitHub **installations**. The mapping is a table (§5). When a webhook arrives, you resolve `installation_id → tenant` to know who the findings belong to.

The GitHub App **private key** is a secret. Store it in the SZEJO **secrets service** (Fernet), never in env files committed to git, never baked into an image.

---

## 2. Stack

Matches SZEJO conventions so it slots into the existing infra with no new primitives.

- **Language/framework:** Python 3.12, FastAPI (matches Mainframe)
- **Datastore:** Postgres 16 (findings, installations, scans), Redis 7 (job queue + short-lived token cache)
- **Queue:** Redis + a worker process. Use `arq` (asyncio-native, Redis-backed) or RQ. `arq` preferred for FastAPI async consistency.
- **Sandbox:** sibling Docker containers via the existing **socket-proxy** (do **not** mount `/var/run/docker.sock` raw — go through socket-proxy, same as `console-api`).
- **HTTP client:** `httpx` (async)
- **JWT:** `PyJWT` with `cryptography` extra (RS256)
- **Images:** `ghcr.io/sz3yan/sentinel-api`, `ghcr.io/sz3yan/sentinel-worker`
- **Routing:** Traefik v3 → cloudflared (prod), Pi-hole + mkcert wildcard (dev)
- **Domains:** `sentinel.sz3yan.com` (prod), `sentinel.dev.sz3yan.com` (dev)
- **Networks:** `orchubi_network` (shared, for Traefik ingress), internal network for api↔worker↔pg↔redis

> ⚠️ **Docker socket = highest-risk surface.** The worker's sandbox capability is the same class of risk you already flagged for `console-api` and slated for WebAuthn MFA gating. Apply the same posture: socket-proxy with a minimal allowed-API surface, no raw socket, network-isolated worker.

---

## 3. Project layout

```
sentinel/
├── api/                        # FastAPI app (ingress, webhooks, UI-facing API)
│   ├── main.py
│   ├── config.py               # settings via pydantic-settings / env
│   ├── github/
│   │   ├── auth.py             # JWT + installation token exchange
│   │   ├── webhooks.py        # signature verify + event routing
│   │   ├── checks.py          # post check runs + annotations
│   │   └── client.py          # thin httpx wrapper, token injection
│   ├── db/
│   │   ├── models.py          # SQLAlchemy models (§5)
│   │   └── session.py
│   └── routes/
│       ├── github_setup.py    # post-install redirect handler
│       └── findings.py        # read API for the dashboard
├── worker/                     # async job processors
│   ├── main.py                # arq worker entrypoint
│   ├── scan.py                # clone → scan → normalize → store → post
│   ├── sandbox.py             # ephemeral container orchestration
│   └── autofix.py             # PHASE 2: finding → patch → PR
├── scanners/                   # scanner adapters → SARIF
│   ├── base.py
│   ├── semgrep.py
│   ├── trivy.py
│   └── gitleaks.py
├── sarif/
│   ├── merge.py               # combine N SARIF docs into one
│   └── fingerprint.py         # stable finding identity for dedup
├── docker/
│   ├── api.Dockerfile
│   ├── worker.Dockerfile
│   └── scanner.Dockerfile     # the image that runs INSIDE the sandbox
├── compose.yaml
├── compose.dev.yaml
└── app-manifest.yml            # GitHub App manifest (§4)
```

---

## 4. GitHub App registration

### 4a. The manifest

Two ways to create the App: the **manifest flow** (you POST this and GitHub scaffolds the App + returns credentials) or **manual** in GitHub settings. Manifest flow is reproducible — prefer it.

`app-manifest.yml`:

```yaml
name: sentinel-security
url: https://sz3yan.com
description: Self-hosted security scanning for your repositories.
public: false          # set true only when opening to external installs
redirect_url: https://sentinel.sz3yan.com/github/setup
hook_attributes:
  url: https://sentinel.sz3yan.com/webhooks/github
  active: true
default_permissions:
  contents: read         # bump to write when AutoFix (phase 2) ships
  pull_requests: write   # open AutoFix PRs
  checks: write          # post pass/fail + inline annotations
  metadata: read         # mandatory baseline
default_events:
  - push
  - pull_request
```

> **Permission discipline:** security buyers read these scopes. `contents: read` lands very differently from `contents: write`. Ship phase 1 read-only. Only request `contents: write` when you actually open fix PRs. The manifest above is phase-1 correct except `pull_requests`/`checks` write, which you need from day one to post results.

### 4b. The manifest-flow handshake

1. Render an HTML form that POSTs the manifest (as a JSON string) to `https://github.com/settings/apps/new?state=<csrf>`.
2. GitHub redirects back to your `redirect_url` with a temporary `?code=`.
3. Your backend exchanges it once: `POST https://api.github.com/app-manifest/{code}/conversions`.
4. The response contains the **App ID**, **client secret**, **webhook secret**, and **PEM private key**. Persist the App ID + webhook secret in config, and the **PEM into the Fernet secrets service**. The temporary `code` expires in one hour.

After the App exists, the **install** page is `https://github.com/apps/sentinel-security/installations/new`. That is the link a user clicks to "install into their repo."

### 4c. What install produces

User clicks Install → picks all-repos or selected repos → approves scopes → GitHub redirects to `redirect_url` with `?installation_id=<id>&setup_action=install`. Your `github_setup` route stores `installation_id` against the logged-in Mainframe tenant. **No credentials change hands. The user never sees a token.**

---

## 5. Database schema (Postgres 16)

```sql
-- a GitHub App installation, owned by a Mainframe tenant
CREATE TABLE installations (
    id                  BIGSERIAL PRIMARY KEY,
    github_installation_id  BIGINT UNIQUE NOT NULL,
    tenant_id           UUID NOT NULL,              -- FK to Mainframe tenant
    account_login       TEXT NOT NULL,              -- org/user the app is installed on
    suspended           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repositories (
    id                  BIGSERIAL PRIMARY KEY,
    installation_id     BIGINT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    github_repo_id      BIGINT UNIQUE NOT NULL,
    full_name           TEXT NOT NULL,              -- "owner/repo"
    default_branch      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE scans (
    id                  BIGSERIAL PRIMARY KEY,
    repository_id       BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    commit_sha          TEXT NOT NULL,
    ref                 TEXT,                        -- refs/heads/main, PR head ref, etc.
    trigger             TEXT NOT NULL,               -- 'push' | 'pull_request'
    status              TEXT NOT NULL DEFAULT 'queued', -- queued|running|done|error
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    check_run_id        BIGINT,                      -- GitHub check run id for status updates
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE findings (
    id                  BIGSERIAL PRIMARY KEY,
    repository_id       BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    fingerprint         TEXT NOT NULL,               -- stable identity (§7) for dedup across scans
    scanner             TEXT NOT NULL,               -- 'semgrep'|'trivy'|'gitleaks'
    rule_id             TEXT NOT NULL,
    severity            TEXT,                        -- normalized: critical|high|medium|low|info
    file_path           TEXT,
    start_line          INT,
    end_line            INT,
    message             TEXT,
    sarif               JSONB NOT NULL,              -- the raw SARIF result object
    state               TEXT NOT NULL DEFAULT 'open', -- open|fixed|ignored|false_positive
    first_seen_scan     BIGINT REFERENCES scans(id),
    last_seen_scan      BIGINT REFERENCES scans(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repository_id, fingerprint)
);

CREATE INDEX idx_findings_repo_state ON findings(repository_id, state);
```

Dedup model: a finding's identity is its `fingerprint` (§7). On each scan, upsert by `(repository_id, fingerprint)` — new fingerprints are inserted `open`, fingerprints absent from a full-repo scan transition to `fixed`. This is what makes "the same vuln reported once across scans" work.

---

## 6. The runtime loop (end to end)

```
GitHub push/PR
   │
   ▼
POST /webhooks/github ──► verify HMAC ──► enqueue {repo, sha, ref} ──► return 200   (api, <2s)
                                              │
                                              ▼ (Redis)
                                         scan worker
                                              │
                  ┌───────────────────────────┼───────────────────────────┐
                  ▼                            ▼                           ▼
        mint installation token        clone into sandbox          post "in_progress"
        (RS256 JWT → token)            (x-access-token)              check run
                  │                            │
                  ▼                            ▼
            run scanners (semgrep, trivy, gitleaks) ──► each emits SARIF
                  │
                  ▼
            merge SARIF + fingerprint + upsert findings (Postgres)
                  │
                  ▼
            update check run: conclusion=success|failure + annotations
                  │
                  ▼
            destroy sandbox
```

---

## 7. Copy-paste scaffolding

### 7a. Webhook signature verification (`api/github/webhooks.py`)

GitHub signs each delivery with HMAC-SHA256 over the raw body, in header `X-Hub-Signature-256`. **Verify against the raw bytes** — re-serializing the parsed JSON will not match.

```python
import hmac
import hashlib
from fastapi import APIRouter, Request, Header, HTTPException

router = APIRouter()

def verify_signature(payload_body: bytes, secret: str, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)

@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    raw = await request.body()                      # RAW bytes — do not use request.json() for verification
    if not verify_signature(raw, settings.GITHUB_WEBHOOK_SECRET, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = await request.json()

    if x_github_event == "push":
        await enqueue_scan_from_push(payload)
    elif x_github_event == "pull_request":
        if payload["action"] in ("opened", "synchronize", "reopened"):
            await enqueue_scan_from_pr(payload)
    elif x_github_event == "installation":
        await handle_installation_lifecycle(payload)   # created/deleted/suspend/unsuspend
    # ack fast; all real work is enqueued
    return {"ok": True}
```

### 7b. The token dance (`api/github/auth.py`)

Sign a short-lived JWT *as the App* (RS256), exchange it for an *installation token* scoped to one installation. Cache the installation token in Redis until ~5 min before its 1-hour expiry.

```python
import time
import jwt           # PyJWT
import httpx

GITHUB_API = "https://api.github.com"

def app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,      # backdate 60s to tolerate clock skew
        "exp": now + 540,     # max 10 min; keep under it
        "iss": app_id,        # the GitHub App ID
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")  # RS256 is required by GitHub

async def installation_token(app_id: str, private_key_pem: str, installation_id: int) -> str:
    # check Redis cache first: key f"ghtok:{installation_id}"
    cached = await redis.get(f"ghtok:{installation_id}")
    if cached:
        return cached.decode()

    jwt_token = app_jwt(app_id, private_key_pem)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    r.raise_for_status()
    data = r.json()
    token = data["token"]                # expires in ~1 hour (data["expires_at"])
    await redis.set(f"ghtok:{installation_id}", token, ex=3000)  # 50 min
    return token
```

> The PEM comes from the **secrets service** at call time. Never hold it in a module global longer than needed.

### 7c. Ephemeral sandbox + clone (`worker/sandbox.py`)

Clone using the installation token as the password with username `x-access-token`. Run each scanner in a throwaway container off the `scanner.Dockerfile` image, mounting the clone read-only where possible.

```python
import asyncio
import tempfile
import shutil
from pathlib import Path

async def with_repo_checkout(full_name: str, sha: str, token: str):
    """Clone a single commit shallow into a temp dir; caller scans, we clean up."""
    workdir = Path(tempfile.mkdtemp(prefix="sentinel-"))
    clone_url = f"https://x-access-token:{token}@github.com/{full_name}.git"
    try:
        # shallow + single commit: minimal data, fast
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", clone_url, str(workdir),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"clone failed: {err.decode()[:500]}")
        # detached checkout of the exact sha (clone gives the branch tip; fetch the sha if PR head differs)
        return workdir
    finally:
        # caller is responsible for calling cleanup() after scanning
        pass

def cleanup(workdir: Path):
    shutil.rmtree(workdir, ignore_errors=True)
```

> **Scrub the token from any logged clone URL.** Never log `clone_url`. If a scanner echoes its invocation, ensure the token isn't in argv it prints.

### 7d. Scanner adapters → SARIF (`scanners/`)

All three emit SARIF natively. The adapter's only job is: run, capture the SARIF file, hand back a parsed dict.

```python
# scanners/semgrep.py
async def run_semgrep(path: str) -> dict:
    out = f"{path}/.sentinel-semgrep.sarif"
    proc = await asyncio.create_subprocess_exec(
        "semgrep", "scan", "--config", "auto", "--sarif", "--output", out, path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return load_sarif(out)

# scanners/trivy.py  — filesystem scan, SARIF template
async def run_trivy(path: str) -> dict:
    out = f"{path}/.sentinel-trivy.sarif"
    proc = await asyncio.create_subprocess_exec(
        "trivy", "fs", "--format", "sarif", "--output", out, path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return load_sarif(out)

# scanners/gitleaks.py
async def run_gitleaks(path: str) -> dict:
    out = f"{path}/.sentinel-gitleaks.sarif"
    proc = await asyncio.create_subprocess_exec(
        "gitleaks", "detect", "--source", path,
        "--report-format", "sarif", "--report-path", out, "--exit-code", "0",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return load_sarif(out)
```

`load_sarif(path)` reads the file, returns `{}` shaped as an empty SARIF run if the file is missing (a clean scan still produces a valid empty result set).

### 7e. SARIF merge + fingerprint (`sarif/`)

```python
# sarif/merge.py — combine N SARIF docs into one document with multiple runs
def merge_sarif(docs: list[dict]) -> dict:
    runs = []
    for d in docs:
        runs.extend(d.get("runs", []))
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": runs,
    }

# sarif/fingerprint.py — stable identity so the same issue dedups across scans
import hashlib
def fingerprint(scanner: str, rule_id: str, file_path: str, snippet: str | None) -> str:
    # prefer SARIF partialFingerprints when the tool provides them; else derive one.
    # deliberately exclude line numbers so cosmetic line shifts don't create "new" findings.
    basis = f"{scanner}::{rule_id}::{file_path}::{(snippet or '').strip()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]
```

> **Fingerprint design matters.** Excluding line numbers means moving a function down 10 lines doesn't resurface a "new" finding. If the scanner emits `partialFingerprints`, use them — they're the tool author's stable identity and beat anything you derive.

### 7f. Post the check run (`api/github/checks.py`)

Create a check run on the head SHA, then update it. SARIF results become **annotations** (GitHub caps at 50 annotations per update — batch if more).

```python
async def create_check_run(full_name: str, sha: str, token: str) -> int:
    async with client(token) as c:
        r = await c.post(f"/repos/{full_name}/check-runs", json={
            "name": "Sentinel Security",
            "head_sha": sha,
            "status": "in_progress",
        })
    r.raise_for_status()
    return r.json()["id"]

async def complete_check_run(full_name: str, check_run_id: int, token: str,
                             findings: list[dict]) -> None:
    annotations = [{
        "path": f["file_path"],
        "start_line": f["start_line"] or 1,
        "end_line": f["end_line"] or f["start_line"] or 1,
        "annotation_level": "failure" if f["severity"] in ("critical", "high") else "warning",
        "message": f["message"],
        "title": f"{f['scanner']}: {f['rule_id']}",
    } for f in findings[:50]]

    conclusion = "failure" if any(
        f["severity"] in ("critical", "high") for f in findings
    ) else "success"

    async with client(token) as c:
        await c.patch(f"/repos/{full_name}/check-runs/{check_run_id}", json={
            "status": "completed",
            "conclusion": conclusion,                  # drives PR gating
            "output": {
                "title": f"{len(findings)} findings",
                "summary": render_summary_md(findings),
                "annotations": annotations,
            },
        })
```

`conclusion: failure` is what lets a branch protection rule **gate the merge** on Sentinel — that's your "stop insecure code before it merges" surface, equivalent to Aikido's PR gating.

---

## 8. Phase 2 — AutoFix (BYO-key)

Deferred, but the shape so the agent can stub the interface now.

```
finding (from Postgres)
   │
   ▼
build context: file contents + N surrounding lines + rule description + scanner message
   │
   ▼
LLM call  ── model + key come from TENANT CONFIG (BYO), not platform ──►  unified diff
   │
   ▼
apply patch on a new branch  →  commit  →  open PR  →  link PR to finding
```

Key points that make this the differentiator, not a clone:
- **Model + API key are tenant-supplied** (Anthropic key, or a local/self-hosted endpoint URL). The platform passes them through; it never bills inference. This is why auto-fix can be unlimited.
- **Two fix classes, very different difficulty:**
  - *Dependency upgrades* — deterministic. Resolve the minimum non-vulnerable version, bump the manifest/lockfile, open PR. Little or no LLM needed. Do this first.
  - *SAST patches* — LLM-generated unified diff, minimal, reviewable. Harder; gate behind a confidence label and always open as a PR a human reviews (never auto-merge by default).
- Mirror Aikido's PR ergonomics: one PR per logical fix, configurable title/branch prefix, draft-PR option, "create task in tracker" hook (their Notion/Jira integration equivalent — you have a Notion pipeline already).

> Auto-fix requires bumping the App permission to `contents: write`. Don't request it until this phase ships.

---

## 9. Phasing

**P0 — solo, today (no App registration).** Hardcode a fine-grained PAT (scoped to *your* repos: Contents read, PRs write, Checks write). Build §7c–§7f: clone → scan → merge → post check run, driven manually or by a simple poll. This proves the entire scanning core with zero GitHub App ceremony. The scanning code is identical to P1 — only the token source changes.

**P1 — the GitHub App + real install flow.** Register the App (§4), implement the webhook receiver (§7a), the token dance (§7b), installation lifecycle handling, and the tenant↔installation mapping. Now other people can install Sentinel. Read-only scanning, check runs, PR gating. This is feature-parity with Aikido's core scanning product.

**P2 — AutoFix.** Bump to `contents: write`, ship dependency-upgrade PRs first (deterministic), then LLM SAST patches (BYO-key). This is the paywall-free differentiator.

**P3 — breadth.** Add scanners (Checkov for IaC, Grype, more) — pure adapter work onto the SARIF spine. Add reachability/triage to cut noise (Aikido's real moat #1; harder, do last).

---

## 10. Compose sketch (fits existing SZEJO infra)

```yaml
# compose.yaml (prod) — abbreviated; wire into existing Traefik + cloudflared + socket-proxy
services:
  sentinel-api:
    image: ghcr.io/sz3yan/sentinel-api:latest
    environment:
      - GITHUB_APP_ID
      - GITHUB_WEBHOOK_SECRET           # from secrets service at deploy
      - DATABASE_URL
      - REDIS_URL
      - OIDC_ISSUER=https://mainframe.sz3yan.com
    networks: [orchubi_network, sentinel-internal]
    labels:
      - traefik.enable=true
      - traefik.http.routers.sentinel.rule=Host(`sentinel.sz3yan.com`)
      - traefik.http.routers.sentinel.entrypoints=websecure
    depends_on: [sentinel-pg, sentinel-redis]

  sentinel-worker:
    image: ghcr.io/sz3yan/sentinel-worker:latest
    environment:
      - DATABASE_URL
      - REDIS_URL
      - DOCKER_HOST=tcp://socket-proxy:2375   # NEVER mount the raw docker.sock
    networks: [sentinel-internal, socket-proxy-net]
    depends_on: [sentinel-redis, sentinel-pg]

  sentinel-pg:
    image: postgres:16
    environment: [POSTGRES_DB=sentinel, POSTGRES_USER, POSTGRES_PASSWORD]
    volumes: [sentinel_pg_data:/var/lib/postgresql/data]
    networks: [sentinel-internal]

  sentinel-redis:
    image: redis:7
    networks: [sentinel-internal]

volumes:
  sentinel_pg_data:

networks:
  sentinel-internal:
    internal: true
  orchubi_network:
    external: true
  socket-proxy-net:
    external: true
```

> Add `sentinel_pg_data` to the nightly `pg_dump` job and RUNBOOK you already run for `mainframe_pg_data`.

---

## 11. Security checklist (review before P1 ships)

- [ ] Webhook HMAC verified against **raw bytes**, constant-time compare.
- [ ] App private key only ever read from the **Fernet secrets service**; never in env files, never in an image layer, never logged.
- [ ] Installation tokens cached with TTL < their expiry; never written to logs.
- [ ] Clone URLs containing `x-access-token:<token>` are **never logged**.
- [ ] Worker reaches Docker **only via socket-proxy** with a minimal allowed API; raw `docker.sock` is not mounted. Same posture as `console-api`.
- [ ] Sandbox containers are network-isolated (scanners don't need outbound except trivy's vuln DB — pin/mirror it).
- [ ] Temp checkouts are deleted in a `finally`, even on scan error.
- [ ] Untrusted repo content is treated as hostile input — scanners run as non-root in the sandbox image.
- [ ] `contents: write` is **not** requested until AutoFix ships.
- [ ] Per-tenant rate limiting on webhook-triggered scans (a force-push loop shouldn't DoS the worker).

---

## 12. Open questions to settle before coding

1. ~~**Final service name** (replaces `aegis` everywhere).~~ **Resolved: `szejo-scan`, later renamed `sentinel`** (the `aegis` placeholder collided with the existing Certificate Manager service; `sentinel` follows the Janus/Aegis one-word mythic-brand naming convention).
2. **Queue lib:** `arq` vs RQ — `arq` recommended for async parity.
3. **PR-head scanning:** scanning a PR head SHA may need a `git fetch` of the PR ref beyond the shallow clone of the default branch — confirm the checkout strategy for forks (token can't push to a fork's head).
4. **Trivy DB in air-gapped mode:** decide on mirroring the vuln DB internally vs allowing the sandbox limited outbound to Trivy's DB registry. This directly affects the air-gapped pitch.
5. **Dashboard:** reuse the existing console UI shell, or a standalone Next.js front-end at `sentinel.sz3yan.com`?

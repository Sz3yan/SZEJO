# AGENTS.md

---

ORCHUBI SERVER (Windows 11 Pro + WSL2)

    - the main host where SZEJO is hosted on. 
    - specifications 
        
        Intel Core i9-13900K (24-core, 3.0 GHz)
        32 GB RAM
        NVIDIA GeForce RTX 3060 12 GB
        954 GB NVMe

        available for CUDA workloads inside containers via `docker run --gpus all ...` (requires nvidia-container-toolkit configured in the WSL2 backend).

    - everything is running in WSL2 
    - Windows host paths are `C:\Users\Sze Yan\...`. Inside WSL2/containers, the Windows drive mounts at `/mnt/c/`; the project root becomes `/mnt/c/Users/Sze Yan/Downloads/SZEJO`.

---

SZE YAN's CONSTITUTION

Behavioral guidelines to reduce common LLM coding mistakes. 

Tradeoff: 
- These guidelines bias toward caution over speed. 
- For trivial tasks, use judgment.

These guidelines are working if: 
- fewer unnecessary changes in diffs, 
- fewer rewrites due to overcomplication
- clarifying questions come before implementation rather than after mistakes

1. Think Before Coding (Don't assume. Don't hide confusion. Surface tradeoffs)

    Before implementing:
    - State your assumptions explicitly. If uncertain, ask.
    - If multiple interpretations exist, present them - don't pick silently.
    - If a simpler approach exists, say so. Push back when warranted.
    - If something is unclear, stop. Name what's confusing. Ask.

    Make use of Multi-Agent Workflows (if needed)
    - orchestrator runs Opus 4.8 efforts max and delegates subtasks to Sonnet 4.6 effort high sub-agents

2. Simplicity First (Minimum code that solves the problem. Nothing speculative)

    - No features beyond what was asked.
    - No abstractions for single-use code.
    - No "flexibility" or "configurability" that wasn't requested.
    - No error handling for impossible scenarios.
    - If you write 200 lines and it could be 50, rewrite it.

    Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

    Make use of the caveman plugin to save token cost. 

3. Surgical Changes (Touch only what you must. Clean up only your own mess.)

    When editing existing code:
    - Don't "improve" adjacent code, comments, or formatting.
    - Don't refactor things that aren't broken.
    - Match existing style, even if you'd do it differently.
    - If you notice unrelated dead code, mention it - don't delete it.

    When your changes create orphans:
    - Remove imports/variables/functions that YOUR changes made unused.
    - Don't remove pre-existing dead code unless asked.

    The test: Every changed line should trace directly to the user's request.

    All to be in docker environment
    - host only have the source code
    - testing, installing packages, linting to be done in docker environment

4. Goal-Driven Execution (Define success criteria. Loop until verified)

    Transform tasks into verifiable goals:
    - "Add validation" → "Write tests for invalid inputs, then make them pass"
    - "Fix the bug" → "Write a test that reproduces it, then make it pass"
    - "Refactor X" → "Ensure tests pass before and after"

    For multi-step tasks, state a brief plan:
    ```
    1. [Step] → verify: [check]
    2. [Step] → verify: [check]
    3. [Step] → verify: [check]
    ```

    Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

5. Security First design (Design secure)

    Every code change must be checked against the 12 categories listed in `SZEJO/SECURITY.md`. 

    If a change touches any of these surfaces, the response must 
        - name the category
        - state the prevention measure applied inline with the diff
        - explicitly call out anything left as a known gap rather than silently shipping it.

6. Scripts in Python (No shell scripts. Python instead.)

    - All new automation/scripts shall be written in Python, not shell (.sh).
    - Applies to new scripts. Don't rewrite existing shell scripts unless asked.

---

SZEJO CLI (szejo-control-plane operator tool)

Run from `szejo-control-plane/`. Invoked as:
  - `python3 -m scripts.szejo <cmd>`  (always works)
  - `szejo <cmd>`                     (if pip-installed: `pip install -e .`)

Most subcommands need decrypted secrets. Chain them through:
  `python3 -m scripts.szejo secrets run -- <any-cmd-here>`

This decrypts `.env.enc` into memory and injects all vars as env — plaintext never touches disk.

### Subcommands

**secrets** — CRUD on `.env.enc` (SOPS+age encrypted)
  - `secrets run [--tf] -- <cmd>`   inject decrypted env into any command; `--tf` also exports TF_VAR_*
  - `secrets get KEY`               print one value
  - `secrets set KEY [VALUE]`       set a value (prompts if VALUE omitted)
  - `secrets rotate KEY [--restart]` regenerate a generated secret; `--restart` recreates affected compose services
  - `secrets edit`                  open `.env.enc` in $EDITOR via sops
  - `secrets encrypt`               encrypt `.env` → `.env.enc`, delete plaintext
  - `secrets decrypt`               decrypt `.env.enc` → `.env`
  - `secrets load`                  print decrypted `.env.enc` to stdout

**bootstrap** — provision secrets/credentials before `docker compose up -d`
  - Idempotent. Generates stable secrets once, rotates admin password unless passkeys registered.
  - `bootstrap [--force] [--apply] [--sync-downstream]`

**setup** — full-stack provisioning (bootstrap → terraform → compose up → crowdsec)
  - Single command for fresh environments. Safe to re-run.
  - `szejo secrets run -- python3 -m scripts.szejo setup`

**certs** — Root CA / intermediates (Fulcio, client-auth) + Aegis-issued internal TLS
  - `certs root`                    create self-signed Root CA (idempotent-guarded)
  - `certs intermediate <kind>`     issue per-purpose intermediate (fulcio | client)
  - `certs all`                     root + fulcio intermediate (deploy-gate bootstrap)
  - `certs issue [...]`             issue internal TLS cert from Aegis
  - `certs renew [...]`             same as issue, cron-friendly (schedule monthly)

**coder** — push Coder workspace templates; single source of truth for template variables
  - `coder push [template...]`      push one or all templates (szejo-base, szejo-atlas, szejo-portfolio)
  - Variables read from decrypted env automatically — always chain via `secrets run`
  - Example: `python3 -m scripts.szejo secrets run -- python3 -m scripts.szejo coder push szejo-portfolio`

**deploy** — cosign-gated deploy (replaces Watchtower for signed services)
  - `deploy verify [service] [image-repo] [pubkey]`
  - Pulls :latest, refuses deploy unless cosign verifies image was signed by per-service key in Janus
  - Fail-closed: any verification error leaves running container untouched
  - Installed as `szejo-deploy-verify.timer` systemd unit (runs every 2 min) by `szejo setup`

### Common patterns

```bash
# Run any command with secrets injected
python3 -m scripts.szejo secrets run -- <cmd>

# Push a single Coder template
python3 -m scripts.szejo secrets run -- python3 -m scripts.szejo coder push szejo-portfolio

# Push all Coder templates
python3 -m scripts.szejo secrets run -- python3 -m scripts.szejo coder push

# Rotate a secret and restart affected services
python3 -m scripts.szejo secrets rotate MAINFRAME_SECRET_KEY --restart

# Get a single secret value
python3 -m scripts.szejo secrets get CODER_SESSION_TOKEN

# Run terraform with secrets injected as TF_VAR_*
python3 -m scripts.szejo secrets run --tf -- terraform apply manifest/terraform/
```

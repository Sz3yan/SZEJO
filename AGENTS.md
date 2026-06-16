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

2. Simplicity First (Minimum code that solves the problem. Nothing speculative)

    - No features beyond what was asked.
    - No abstractions for single-use code.
    - No "flexibility" or "configurability" that wasn't requested.
    - No error handling for impossible scenarios.
    - If you write 200 lines and it could be 50, rewrite it.

    Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

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

    Consult `SECURITY.md`, when designing 

1. Before writing or modifying any code, consult `SECURITY.md` at the repo root. It lists 12 attack categories (BOLA/IDOR, business logic & validation, code/command injection, SQLi/NoSQLi, LLM/prompt injection, SSRF, auth/session, client-side XSS/CSRF/open-redirect, deserialization/SSTI, files/misconfig, secrets/crypto, hardening/CORS/TLS/headers) and the concrete prevention rules and repo touchpoints for each. Apply them by default — do not wait to be asked. When a change touches any of those surfaces, name the category, state the prevention measure inline with the diff, and call out anything left as a known gap rather than silently shipping it.

2. Use the Caveman plugin to minimize token usage during long sessions (>50k tokens consumed) or when working on Atlas/Mainframe code-heavy tasks. For short Q&A or planning-only sessions, default off.

3. When opening a PR, enable auto-merge so it merges once required status checks pass. This depends on branch protection being configured on `main` with CI, lint, and tests as required checks. If branch protection is not in place on the target repo, do not enable auto-merge — flag it instead so it can be configured first.

4. For multi-agent workflows, the orchestrator runs Opus 4.8 and delegates subtasks to Sonnet 4.6 sub-agents. When executing implementation plans, prefer **subagent-driven development** (superpowers:subagent-driven-development skill): fresh subagent per task, two-stage review (spec compliance then code quality) after each task, continuous execution without pausing for check-ins.

5. Anything that touches services, environment variables from Infisical, network, or the runtime environment runs inside Docker — including integration tests, end-to-end tests, and anything exercising MWDB, Karton, Redis, or Mainframe. Pure static checks with no external dependencies (`ruff`, `mypy`, isolated unit tests) may run on the host for speed.






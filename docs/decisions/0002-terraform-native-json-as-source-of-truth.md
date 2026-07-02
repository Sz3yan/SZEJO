# ADR 0002 — Generate Terraform's native JSON syntax instead of hand-writing HCL

- **Status:** accepted
- **Date:** 2026-07-03
- **Scope:** `szejo-control-plane/manifest/terraform/` (Cloudflare tunnel, DNS, Workers, firewall)

## Context

All Cloudflare infra (tunnel config, 10 DNS records, the maintenance-page
Workers script + routes, an optional firewall ruleset) was hand-written HCL
across `cloudflare.tf`, `variables.tf`, `providers.tf`, `outputs.tf`, plus an
unused `backend.tf.example`. A parallel Python script
(`szejo bootstrap --emit-imports`) already had to know the same hostname list
to generate disaster-recovery `import` blocks — two hand-maintained sources
of the same fact (hostname → resource address), which had already drifted
once: a `*.coder.sz3yan.com` wildcard record existed live in Cloudflare but
was never committed, because an earlier revert of an unrelated HCL change
blindly discarded it.

## Decision

Terraform accepts a JSON-encoded alternative to HCL for any `.tf.json` file —
same semantics, same `${...}` expression syntax inside strings, fully
mixable with `.tf` files in the same directory. We now generate
`manifest/terraform/cloudflare.tf.json` from a plain Python dict
(`szejo_cli/tf_generate.py`, run via `szejo terraform generate`) instead of
hand-editing HCL. `variables.tf`, `providers.tf`, `outputs.tf`, and
`backend.tf.example` are deleted — their content (variable declarations,
provider block, required_providers) is generated into the same JSON file, so
the directory holds exactly one hand-authored source of truth
(`tf_generate.py`) and one generated artifact.

- **Why:** a hostname now exists in exactly one place (`_TUNNEL_HOSTNAMES` in
  `tf_generate.py`) instead of two (HCL `locals` block + `tf_imports.py`'s
  own copy). Python data structures (dict comprehensions, list literals) are
  a better fit for "10 near-identical resources" than copy-pasted HCL blocks
  — the whole ingress list, DNS `for_each` map, and Workers route patterns
  are now derived from one dict instead of hand-kept in sync across three
  places in the HCL.
- **Cost (accepted):** JSON-syntax Terraform is less common than HCL, so
  anyone reading `cloudflare.tf.json` directly (rather than through
  `tf_generate.py`) sees `${...}`-wrapped expression strings instead of bare
  HCL — noted in the module's docstring for future readers.
- **Verification:** generated output was diffed against live state via
  `terraform plan` both before and after the migration — "No changes" both
  times, confirming the generated config is behaviorally identical to the
  hand-written HCL it replaced.

Also fixed in the same change: `manifest/terraform/scripts/import-existing.sh`
(an imperative bash equivalent of `szejo bootstrap --emit-imports`) was
deleted — it had already drifted (missing several hostnames the Python
generator has) and duplicated the declarative import-block generator built
earlier in the same session.

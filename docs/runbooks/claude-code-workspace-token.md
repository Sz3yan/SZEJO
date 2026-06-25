# Runbook — Claude Code OAuth token for Coder workspace

Claude Code in the `szejo-portfolio` workspace authenticates via a long-lived
OAuth token injected at workspace build time. This avoids the `localhost`
OAuth callback problem that occurs when running `claude auth login` inside a
remote Coder workspace (the browser redirect can't reach the workspace's
localhost).

Template: `szejo-control-plane/manifest/coder/templates/szejo-portfolio/main.tf`
Module: `registry.coder.com/coder/claude-code/coder` v5.2.0
Secret key: `CLAUDE_OAUTH_TOKEN` in `.env.enc`

---

## Token lifetime

`claude setup-token` tokens are long-lived OAuth refresh tokens. Anthropic does
not publish an exact TTL, but in practice they last months to over a year. The
token silently stops working when it expires — `claude` in the workspace will
prompt for re-auth on next use.

---

## First-time setup

Run this in an **interactive terminal** (with browser access). Inside Claude
Code, use the `!` prefix to get a real TTY:

```
! claude setup-token
```

A browser window opens. Authorize on claude.ai. The command prints a token.
Store it and push the template:

```sh
szejo secrets set CLAUDE_OAUTH_TOKEN <token>
szejo secrets run -- szejo coder push szejo-portfolio
```

Then update the running workspace in the Coder UI (Update button on the
portfolio workspace).

---

## Rotating an expired token

Same steps as first-time setup:

1. In an interactive terminal (or via `!` in Claude Code prompt):

   ```
   ! claude setup-token
   ```

2. Replace the stored secret:

   ```sh
   szejo secrets set CLAUDE_OAUTH_TOKEN <new-token>
   ```

3. Re-push the template:

   ```sh
   szejo secrets run -- szejo coder push szejo-portfolio
   ```

4. Rebuild the workspace: Coder UI → portfolio workspace → **Update**.
   Or via CLI: `coder update portfolio`.

The rebuild writes the new token into the workspace. No downtime beyond the
normal workspace restart (~2 min for DinD startup).

---

## Verifying auth inside the workspace

```sh
claude --version   # confirms installation
claude whoami      # confirms the token is valid and shows the account
```

If `claude whoami` fails with an auth error, the token is expired — rotate it.

---

## Notes

- Token is injected via the `claude_code_oauth_token` input on the
  `coder/claude-code` module. The module writes it as `CLAUDE_CODE_OAUTH_TOKEN`
  in the agent environment.
- The token is a Coder **sensitive template variable** — it is never stored in
  workspace logs or shown in the Coder UI.
- If `CLAUDE_OAUTH_TOKEN` is empty in `.env.enc`, Claude Code is still
  installed but unauthenticated. Auth manually in the workspace with
  `coder port-forward portfolio.main --tcp <port>:<port>` (see below).

---

## Alternative: port-forward auth (no token, one-off)

If you need to auth without regenerating a token (e.g. token source machine
unavailable):

1. In workspace terminal: run `claude` — it prints an OAuth URL with a
   `redirect_uri=http://localhost:<PORT>/...` parameter. Note the port.
2. On local machine: `coder port-forward portfolio.main --tcp <PORT>:<PORT>`
3. Visit the OAuth URL in your browser, authorize.
4. The redirect hits `localhost:<PORT>` on your machine → forwarded to workspace.
5. Claude Code captures the callback and writes `~/.claude.json`.

Credentials persist in the home volume across workspace stop/start, but are
lost on a full rebuild. Use the token injection approach for permanence.

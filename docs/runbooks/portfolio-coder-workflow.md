# Portfolio Coder Workspace — Dev Workflow

Portfolio runs inside a Coder DinD workspace: `Sz3yan/portfolio`.
Source code lives at `github.com/Sz3yan/szejo-portfolio` (separate repo, not this monorepo).

---

## Connecting

```bash
# One-shot command in workspace
coder ssh Sz3yan/portfolio -- "docker ps"

# Interactive shell
coder ssh Sz3yan/portfolio
```

Containers:
| Name | Image | Port |
|------|-------|------|
| `portfolio-app` | `ghcr.io/sz3yan/portfolio-app:latest` | 8080 → sz3yan.com |
| `portfolio-cms` | `ghcr.io/sz3yan/portfolio-cms:latest` | 3000/8081 → cms.sz3yan.com |
| `portfolio-db` | postgres:16 | 5432 (internal) |

---

## Exec into a running container

```bash
# CMS container — useful for payload CLI, migration runs, inspecting build output
coder ssh Sz3yan/portfolio -- "docker exec -it portfolio-cms sh"

# App container (non-interactive)
coder ssh Sz3yan/portfolio -- "docker exec portfolio-app printenv NODE_ENV"
```

---

## Deploying code changes

No Watchtower — nothing polls in the background. Two ways to pick up a new image
after push to `production` (GitHub Actions CI builds it):

Restart the workspace (the template's startup script runs `docker compose pull`
before `up -d` on every start):
```bash
coder stop Sz3yan/portfolio && coder start Sz3yan/portfolio
```

Or pull without restarting the whole workspace:
```bash
coder ssh Sz3yan/portfolio -- "cd ~/szejo-portfolio && docker compose pull portfolio-app portfolio-cms && docker compose up -d portfolio-app portfolio-cms"
```

---

## PayloadCMS migration workflow

Migrations need a live DB connection — they can't run from the host. The workflow:

### 1. Copy new source files into the running CMS container

```bash
# After editing collection files locally in szejo-portfolio/
coder ssh Sz3yan/portfolio -- "docker cp ~/szejo-portfolio/cms/src/collections/. portfolio-cms:/app/src/collections/"
```

### 2. Generate the migration inside the container

```bash
coder ssh Sz3yan/portfolio -- "docker exec portfolio-cms sh -c 'cd /app && node node_modules/.bin/payload migrate:create'"
```

Payload writes a timestamped file: `src/migrations/YYYYMMDD_HHMMSS.ts` + `YYYYMMDD_HHMMSS.json`.

### 3. Copy migration files back out

```bash
MIGRATION_TS="20260624_235629"   # replace with actual timestamp

coder ssh Sz3yan/portfolio -- "docker cp portfolio-cms:/app/src/migrations/${MIGRATION_TS}.ts ~/szejo-portfolio/cms/src/migrations/${MIGRATION_TS}_describe_change.ts"
coder ssh Sz3yan/portfolio -- "docker cp portfolio-cms:/app/src/migrations/${MIGRATION_TS}.json ~/szejo-portfolio/cms/src/migrations/${MIGRATION_TS}_describe_change.json"
```

### 4. Register migration in index.ts

In `cms/src/migrations/index.ts`:
```typescript
import * as migration_YYYYMMDD_HHMMSS_describe_change from './YYYYMMDD_HHMMSS_describe_change'

export const migrations = [
  // ... existing entries ...
  {
    up: migration_YYYYMMDD_HHMMSS_describe_change.up,
    down: migration_YYYYMMDD_HHMMSS_describe_change.down,
    name: 'YYYYMMDD_HHMMSS_describe_change',
  },
]
```

### 5. Commit and push, then pull in workspace

```bash
# In szejo-portfolio repo — configure git identity if missing
coder ssh Sz3yan/portfolio -- "cd ~/szejo-portfolio && git config user.email 'sz3yan@gmail.com' && git config user.name 'szeyan'"

# Push triggers CI; after it passes, pull new image
coder ssh Sz3yan/portfolio -- "cd ~/szejo-portfolio && git add -A && git commit -m 'feat(cms): ...' && git push origin production"
```

### 6. Migration runs automatically on container start

The CMS container CMD is:
```
node node_modules/payload/bin.js migrate && node server.js
```

Pending migrations applied before server starts. Verify in logs:
```bash
coder ssh Sz3yan/portfolio -- "docker logs portfolio-cms 2>&1 | grep -i migrat"
# Migrated: YYYYMMDD_HHMMSS_describe_change (Xms)
```

---

## Gotchas

**PayloadCMS types stale after adding collections**: `payload generate:types` must run in the Dockerfile *before* `generate:importmap` and `npm run build`. Otherwise `CollectionSlug` in `payload-types.ts` won't include new slugs → TypeScript build failure.

```dockerfile
RUN node node_modules/.bin/payload generate:types
RUN node node_modules/.bin/payload generate:importmap
RUN npm run build
```

**Double-cast for generated Payload types**: After `generate:types`, collection interfaces have no index signature. Use `d as unknown as Record<string, unknown>` not `d as Record<string, unknown>`.

**Git identity missing in workspace**: Coder workspace shell has no git user set by default. Always run `git config user.email/user.name` before committing inside the workspace.

**Sharp warning at runtime**: `Image resizing is enabled but sharp not installed` is non-blocking. Originals upload and display; `imageSizes` thumbnails just aren't generated. Full fix requires `sharp` built for the alpine runtime in the Dockerfile.

---

## Full workspace restart

```bash
coder stop Sz3yan/portfolio && coder start Sz3yan/portfolio
# or
coder restart Sz3yan/portfolio
```

After restart, pull latest images:
```bash
coder ssh Sz3yan/portfolio -- "cd ~/szejo-portfolio && docker compose pull && docker compose up -d"
```

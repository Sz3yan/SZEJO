# Incident — WSL launch hang / control-plane bounce (2026-07-02 ~10:18 SGT)

**Duration:** ~35 min hang (10:18–10:53 SGT) + one deliberate control-plane restart to apply the fix
**Impact:** `wsl` would not open a shell on the ORCHUBI Windows host — every launch hung forever. No coding work possible in WSL2. The 24 control-plane containers stayed *up* throughout the hang (they live in the `docker-desktop` distro, not Ubuntu), but were bounced once during the permanent fix.
**Severity:** Medium — developer/operator access blocked; control plane briefly restarted.

---

## Symptom

- `wsl --status`, `wsl --list --verbose`, `wsl --version` all returned **instantly** and reported Ubuntu as `Running`.
- Actually **attaching** to a distro (`wsl`, `wsl -d Ubuntu -e echo ...`, `wsl whoami`) **hung indefinitely** with zero output.
- Repeated attempts piled up: **14–16 orphaned `wsl.exe` / `wslhost` processes** stacked since ~10:18.
- The pileup wedged the subsystem so hard that even `wsl --shutdown` hung.

---

## Root Cause

All WSL2 distros (**Ubuntu** + **docker-desktop**) share **one utility VM** (`vmmemWSL`). `C:\Users\Sze Yan\.wslconfig` had `networkingMode=mirrored` (+ `firewall=true`). Mirrored networking intermittently hangs **distro-attach** after the shared network stack is disturbed — a Docker Desktop restart/update, sleep/resume, or VPN toggle. When attach stalls, the `wsl` launcher blocks; each retry adds another stuck launcher, and the pileup eventually jams the whole subsystem.

Nothing in the control plane requires mirrored mode: all service traffic is internal Docker bridge networking, and the only host-published ports (Traefik `80`/`443`) are handled by Docker Desktop's own port proxy, which behaves identically under NAT. Mirrored had been enabled speculatively.

Same trigger family as the socket-proxy stale-mount issue (Docker Desktop / WSL restart hiccup destabilizing the shared VM).

---

## Recovery Steps Taken

1. Confirmed the split behavior: management commands fine, distro-attach hung (background `wsl -e echo` produced no output after 8s+).
2. Counted the pileup: `Get-Process wsl,wslhost` → 14–16 stuck launchers; `wsl --shutdown` also hung.
3. Verified services alive: `Get-Service WSLService,vmcompute,HvHost` → all `Running` (nothing dead — just wedged).
4. **Force-killed the stuck launchers** (safe — none had attached, so no data loss):
   ```powershell
   Get-Process wsl,wslhost -ErrorAction SilentlyContinue | Stop-Process -Force
   ```
5. This **unblocked `wsl --shutdown`**, which then completed cleanly (exit 0).
6. Cold-started Ubuntu → `wsl -d Ubuntu -e echo ok` returned `ok` (exit 0). WSL restored.

---

## Permanent Fix Applied

Switched off mirrored networking to stop the attach hangs recurring. Edited `C:\Users\Sze Yan\.wslconfig`:

```ini
[wsl2]
memory=8GB
processors=4
swap=2GB
networkingMode=NAT          # was: mirrored
                            # removed: firewall=true  (mirrored-only setting, moot under NAT)
autoMemoryReclaim=gradual
kernelCommandLine = cgroup_no_v1=all
```

`.wslconfig` is only read on a cold VM start, so activating it required a full `wsl --shutdown` (the one deliberate control-plane bounce).

### Post-fix verification
- `wsl -d Ubuntu -e echo` → launches instantly, exit 0.
- `ip addr show eth0` → `172.25.188.243/20` (NAT subnet — confirms mirrored is gone).
- `docker info` → `engine=docker-desktop running=24 stopped=0`.
- All 24 `szejo-control-plane-*` containers `Up` and every health-checked service `(healthy)`.
- No socket-proxy 503 cascade this time.

---

## Key Fact for Next Time

The control-plane Docker engine runs in the **docker-desktop** distro, **not Ubuntu** (Ubuntu only hosts the `docker` CLI that talks to it). So a wedged Ubuntu can be fixed **without** touching the control plane:

```powershell
Get-Process wsl,wslhost -ErrorAction SilentlyContinue | Stop-Process -Force
wsl --terminate Ubuntu        # single distro — docker-desktop + containers keep running
wsl -d Ubuntu -e echo ok
```

Reserve `wsl --shutdown` (kills the whole VM, incl. docker-desktop → control plane down) for when `--terminate Ubuntu` won't clear the wedge, or when a `.wslconfig` change must be applied.

---

## Prevention / Improvements

- [x] Switched `networkingMode` mirrored → NAT (root-cause fix for the attach hangs).
- [ ] Don't spam `wsl` when it hangs — each attempt adds to the launcher pileup. Kill stuck launchers first.
- [ ] If a hang recurs *despite* NAT, suspect the shared utility VM / Docker Desktop network rather than mirrored mode.
- [ ] Ensure Docker Desktop is set to start on login so containers (`restart: unless-stopped`) auto-recover after any full `wsl --shutdown`.

---

## Useful Diagnosis Commands (PowerShell, Windows host)

```powershell
# Is it the "management-fine / attach-hangs" pattern?
wsl --list --verbose            # returns instantly if only attach is wedged
wsl -d Ubuntu -e echo ok        # hangs => wedged

# Count the launcher pileup
Get-Process wsl,wslhost -ErrorAction SilentlyContinue | Measure-Object

# Confirm services aren't actually dead
Get-Service WSLService,vmcompute,HvHost

# Targeted recovery (keeps control plane up)
Get-Process wsl,wslhost -ErrorAction SilentlyContinue | Stop-Process -Force
wsl --terminate Ubuntu
wsl -d Ubuntu -e echo ok

# Confirm networking mode after a restart (NAT => 172.x on eth0)
wsl -d Ubuntu -e ip -o addr show eth0

# Control-plane health
wsl -d Ubuntu -e docker info --format 'running={{.ContainersRunning}} stopped={{.ContainersStopped}}'
wsl -d Ubuntu -e docker ps --filter name=szejo-control-plane --format '{{.Names}}: {{.Status}}'
```

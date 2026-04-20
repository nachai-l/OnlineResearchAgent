# Running on a Windows corporate machine (Accenture) — WSL2 + Docker Engine

This is a **step-by-step recipe** for standing the agent up on a
locked-down Windows laptop where Docker Desktop is blocked / fails to
install. It documents the exact path we took internally: **WSL2 +
Ubuntu + Docker Engine inside WSL** (no Docker Desktop required).

If Docker Desktop is available to you, prefer the simpler path in the
top-level [README](../README.md#deploy-with-docker) — this doc is only
for the WSL-only workaround.

---

## Why this path?

- `docker` was not on `PATH` in plain PowerShell.
- Docker Desktop install failed with an assertion error under `winget`.
- Running **Docker Engine directly inside WSL2** is permitted, avoids
  the Docker Desktop license / UI dependency, and needs no admin
  approval beyond what WSL itself requires.

---

## One-time setup

### 1. Enable WSL2

In an **elevated PowerShell**:

```powershell
wsl --install
wsl --update
```

Reboot when prompted — this wires up the Windows features
(`VirtualMachinePlatform`, HyperV backbone) that WSL2 needs.

### 2. Install Ubuntu

```powershell
wsl -l -v                  # confirm no distro yet
wsl --install -d Ubuntu
```

The first launch will drop you into Ubuntu and prompt for a default
Unix user + password. Complete the prompt before moving on.

### 3. Install Docker Engine **inside** Ubuntu

From an Ubuntu shell (`wsl -d Ubuntu`):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo service docker start
sudo usermod -aG docker "$USER"     # avoid "permission denied on /var/run/docker.sock"
```

Log out + back into Ubuntu (`exit` then `wsl -d Ubuntu` again) so the
`docker` group membership takes effect. Verify:

```bash
docker run --rm hello-world
docker compose version
```

---

## Running the agent

The project lives on the Windows side at:

```
C:\Users\nachai.limsettho\OneDrive - Accenture\Desktop\Codes\A2A_Agent\OnlineResearchAgent\
```

WSL exposes it as `/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent`.

### From an Ubuntu shell (preferred)

```bash
cd "/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent"
cp .env.example .env                        # first time only — fill keys in
docker compose up --build
```

### From PowerShell (wrap every command with `wsl -d Ubuntu`)

```powershell
wsl -d Ubuntu --cd "/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent" docker compose up --build
```

Or, if you'd rather run the image directly:

```powershell
wsl -d Ubuntu --cd "/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent" docker run --rm -p 8000:8000 -e A2A_PUBLIC_URL=http://localhost:8000 --env-file .env online-research-agent:latest
```

> PowerShell does not honor Bash-style `\` line-continuation. Either
> keep the command on one line (as above), use PowerShell backticks
> `` ` ``, or stay inside the Ubuntu shell.

---

## Smoke test

From Windows (PowerShell) — `curl` in PowerShell is an `Invoke-WebRequest`
alias and will complain about headers, so use `curl.exe` or `irm`:

```powershell
curl.exe http://localhost:8000/.well-known/agent-card.json
# or
irm http://localhost:8000/.well-known/agent-card.json | ConvertTo-Json -Depth 10
```

Or through WSL:

```powershell
wsl bash -lc "curl -s http://localhost:8000/.well-known/agent-card.json | jq -r .url"
# http://localhost:8000
```

End-to-end client smoke:

```powershell
python scripts/client_smoke.py "what is the Google A2A protocol?"
```

You should see `TaskState.working` → `TaskState.completed` and the
markdown summary printed.

> Hitting `http://localhost:8000/` in a browser returns `405 Method Not
> Allowed` — **this is expected**. The root path is a JSON-RPC POST
> endpoint, not a browsable page. The Agent Card at
> `http://localhost:8000/.well-known/agent-card.json` *does* render in
> a browser.

---

## Known issues & fixes

### `docker` not recognized in PowerShell

You have Docker **inside WSL**, not on Windows. Always prefix with
`wsl -d Ubuntu ...` or switch to an Ubuntu shell.

### Permission denied on `/var/run/docker.sock`

Your Ubuntu user isn't in the `docker` group yet. Run:

```bash
sudo usermod -aG docker "$USER"
exit        # then reopen Ubuntu so the group takes effect
```

### Port 8000 already allocated

Another container is publishing port 8000. Stop it:

```powershell
wsl -d Ubuntu bash -lc "docker ps -q --filter publish=8000 | xargs -r docker stop"
```

### `pip` launcher points at a stale venv

Happens after moving / recreating `.venv`. Recreate it, and prefer
`python -m pip ...` over bare `pip`:

```powershell
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Browser shows "Method Not Allowed" at `/`

Not broken — see the smoke-test note above. Use
`/.well-known/agent-card.json` for a browsable health check.

---

## Handy aliases (optional)

Drop into `~/.bashrc` inside Ubuntu to cut the typing:

```bash
alias ora-cd='cd "/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent"'
alias ora-up='ora-cd && docker compose up --build'
alias ora-down='ora-cd && docker compose down'
alias ora-logs='ora-cd && docker compose logs -f'
alias ora-kill8000='docker ps -q --filter publish=8000 | xargs -r docker stop'
```

---

## Key takeaway

Because Docker Engine lives **inside WSL Ubuntu**, every `docker` or
`docker compose` invocation must originate **inside WSL** — either from
an Ubuntu shell directly, or via `wsl -d Ubuntu ...` from PowerShell.
They will not work in bare PowerShell unless you also install Docker
on the Windows side.

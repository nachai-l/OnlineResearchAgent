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

### From PowerShell (recommended)

`cd` into the project first, then let WSL auto-translate your current
Windows path into `/mnt/c/...` via `"$PWD"` — no typing the long path,
no `...` placeholder to accidentally copy verbatim:

```powershell
cd .\OnlineResearchAgent
wsl -d Ubuntu --cd "$PWD" docker compose up --build
```

Verify the translation works once:

```powershell
wsl -d Ubuntu --cd "$PWD" pwd
# /mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent
```

Or, if you'd rather run the image directly without compose:

```powershell
wsl -d Ubuntu --cd "$PWD" docker run --rm -p 8000:8000 -e A2A_PUBLIC_URL=http://localhost:8000 --env-file .env online-research-agent:latest
```

> PowerShell does not honor Bash-style `\` line-continuation. Either
> keep the command on one line (as above), use PowerShell backticks
> `` ` ``, or stay inside the Ubuntu shell.

---

## Smoke test

### 1. Health check — Agent Card

PowerShell's `curl` is an `Invoke-WebRequest` alias and mangles headers;
use **`curl.exe`** (the real curl) or `irm`:

```powershell
curl.exe http://localhost:8000/.well-known/agent-card.json
# or, parsed + pretty:
irm http://localhost:8000/.well-known/agent-card.json | ConvertTo-Json -Depth 10
```

### 2. Swagger UI (the easy path)

Open `http://localhost:8000/docs` in a browser. For each `POST`
operation, click **Try it out**, pick a method from the **Examples**
dropdown (`message/send`, `message/stream`, `tasks/get`,
`tasks/cancel`), then **Execute**. No curl, no quoting.

> `http://localhost:8000/` in a browser returns `405 Method Not Allowed`
> — **this is expected**. The root path is JSON-RPC POST only.
> `/.well-known/agent-card.json` and `/docs` *do* render in a browser.

### 3. JSON-RPC with curl — use `--data-binary @file`

PowerShell has a long-standing bug where it strips embedded `"` when
passing string arguments to native `.exe` programs, so
`curl.exe --data-raw $body` sends mangled JSON and the server responds
with `{"error":{"code":-32700,"message":"Expecting property name ..."}}`.

The workaround is to write the body to a file and use
`--data-binary @file` — curl reads it verbatim and never touches the
quotes:

```powershell
@"
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "What is the Google A2A protocol?"}],
      "messageId": "msg-1"
    }
  }
}
"@ | Out-File -Encoding ascii -NoNewline body.json

curl.exe -X POST http://localhost:8000/ `
  -H "Content-Type: application/json" `
  --data-binary "@body.json"
```

**Use `-Encoding ascii`** (not `utf8`) — a UTF-8 BOM trips the JSON-RPC
parser too.

### 4. JSON-RPC with `Invoke-RestMethod` (pure PowerShell)

Lets PowerShell do the JSON serialization itself — no curl, no quote
hand-off, no file:

```powershell
$body = @{
  jsonrpc = "2.0"
  id      = "1"
  method  = "message/send"
  params  = @{
    message = @{
      role      = "user"
      parts     = @(@{ kind = "text"; text = "What is the Google A2A protocol?" })
      messageId = "msg-1"
    }
  }
} | ConvertTo-Json -Depth 10

irm -Method Post -Uri http://localhost:8000/ -ContentType "application/json" -Body $body
```

### 5. End-to-end client (recommended for most runs)

The shipped smoke script handles Agent Card discovery, JSON-RPC framing,
and streaming for you:

```powershell
python .\scripts\client_smoke.py "what is the Google A2A protocol?"
```

Expect `TaskState.working` → `TaskState.completed` plus the markdown
summary and numbered sources. First run takes 20–60 s (live SERP +
scrape + 3 LLM calls); a repeated query returns in < 1 s (all three
JSONL caches hit).

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
`/.well-known/agent-card.json` or `/docs` for browsable endpoints.

### `curl.exe --data-raw $body` → server returns JSON-RPC parse error

Symptom:

```json
{"error":{"code":-32700,"message":"Expecting property name enclosed in double quotes: line 2 column 3 (char 4)"},"jsonrpc":"2.0"}
```

PowerShell strips the embedded `"` characters when passing `$body` to
native `.exe` programs, so curl sends `{ jsonrpc: 2.0, ... }` (unquoted
keys) and the server rejects it.

Use **`--data-binary @body.json`** (see *Smoke test* section 3) or
**`Invoke-RestMethod`** (section 4). Both avoid the quote hand-off to
curl entirely.

### `ModuleNotFoundError: No module named 'a2a.server.apps'` inside the container

The running image was built against a newer `a2a-sdk` that refactored
its module layout. The repo now pins `>=0.3.26,<0.4.0` in
`requirements.txt`. Force a clean rebuild:

```powershell
wsl -d Ubuntu --cd "$PWD" docker compose down
wsl -d Ubuntu --cd "$PWD" docker compose build --no-cache
wsl -d Ubuntu --cd "$PWD" docker compose up
```

### `/mnt/c/.../OnlineResearchAgent` → `chdir() failed 2`

You pasted the `...` placeholder verbatim. Use `"$PWD"` after
`cd`'ing into the project folder, or spell out the full path:
`/mnt/c/Users/nachai.limsettho/OneDrive - Accenture/Desktop/Codes/A2A_Agent/OnlineResearchAgent`.

---

## Cheat-sheet (tested)

Run from `C:\...\A2A_Agent\OnlineResearchAgent\` in PowerShell with the
venv activated. Each block is self-contained.

| Step | Command |
|------|---------|
| **1. Start** | `wsl -d Ubuntu --cd "$PWD" docker compose up --build` |
| **1. Start (detached)** | `wsl -d Ubuntu --cd "$PWD" docker compose up --build -d` |
| **1. Tail logs** | `wsl -d Ubuntu --cd "$PWD" docker compose logs -f` |
| **2. Swagger UI** | Browser → `http://localhost:8000/docs` |
| **3a. Curl — Agent Card** | `curl.exe http://localhost:8000/.well-known/agent-card.json` |
| **3b. Curl — `message/send`** | write body via `Out-File -Encoding ascii -NoNewline body.json` (see block below), then `curl.exe -X POST http://localhost:8000/ -H "Content-Type: application/json" --data-binary "@body.json"` |
| **3c. PowerShell-native** | `irm -Method Post -Uri http://localhost:8000/ -ContentType "application/json" -Body $body` (see block below) |
| **3d. Client smoke** | `python .\scripts\client_smoke.py "your query"` |
| **Stop** | `wsl -d Ubuntu --cd "$PWD" docker compose down` |
| **Force-kill port 8000** | `wsl -d Ubuntu bash -lc "docker ps -q --filter publish=8000 \| xargs -r docker stop"` |
| **Rebuild (no cache)** | `wsl -d Ubuntu --cd "$PWD" docker compose build --no-cache` |

### `body.json` for step 3b

```powershell
@"
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "What is the Google A2A protocol?"}],
      "messageId": "msg-1"
    }
  }
}
"@ | Out-File -Encoding ascii -NoNewline body.json
```

### `$body` hashtable for step 3c

```powershell
$body = @{
  jsonrpc = "2.0"
  id      = "1"
  method  = "message/send"
  params  = @{
    message = @{
      role      = "user"
      parts     = @(@{ kind = "text"; text = "What is the Google A2A protocol?" })
      messageId = "msg-1"
    }
  }
} | ConvertTo-Json -Depth 10
```

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

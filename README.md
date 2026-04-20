# Online Research Agent (A2A)

**Deterministic web-research workflow agent** (not a tool-use loop) exposed
over Google's **A2A (Agent2Agent) protocol**. On each query it runs a
fixed 5-stage pipeline:

    SerpAPI search (→ Google CSE fallback)
        → LLM-picked URLs
            → concurrent scrape
                → LLM-scored relevance + trustworthiness gate
                    → LLM grounded summary with inline [n] citations

Every external call (search, scrape, LLM) is **JSONL-cached** with a
canonical-JSON SHA-256 key, so a repeated query is served with **zero
HTTP and zero LLM calls**. Every LLM JSON response is **pydantic-validated**
before reaching business logic; schema or transport failures share a
single retry budget.

Built as the canonical example for our agent catalogue — intentionally
higher-bar than an ad-hoc script: **test-driven**, **JSONL-cached at
every layer**, **structured-JSON logging**, **pydantic-validated LLM
output**, and **fully typed settings**.

---

## Why this layout

| Concern | How it's handled |
|---|---|
| Correctness | TDD per module, 116 tests, 91% coverage. No module was written before its test was red. Every LLM JSON response is validated against a pydantic schema before reaching business logic. |
| Reproducibility | Every external call (SERP, scrape, LLM) is cached in an append-only JSONL file. Second run of the same query issues zero HTTP + zero LLM calls. |
| Debuggability | Every stage emits a structured log line (`{ts, level, logger, msg, stage, …}`) to a rotating file + stderr. |
| Swap-ability | Every subcomponent is dependency-injected. Tests use a stub `call_fn` for Gemini and `respx` for HTTP. No vendor SDK is imported at module-load time. |
| Secrets hygiene | YAML stores only env-var **names**. Real keys live in `.env` (gitignored). Container / CI env wins over `.env` via `load_dotenv(override=False)`. |

---

## Pipeline stages

Fixed order, executed sequentially by `ResearchPipeline.run(query)`. The
LLM is a component *inside* the workflow (invoked at stages 2, 4, 5),
never the orchestrator.

| # | Stage      | Component         | Scale                                              | LLM? |
|---|------------|-------------------|----------------------------------------------------|------|
| 1 | Search     | `WebSearcher`     | `top_k_results` (8) — SerpAPI, Google CSE fallback | no   |
| 2 | Select     | `ResultSelector`  | picks ≤ `top_k_to_scrape` (3) indices out of 8     | yes  |
| 3 | Scrape     | `WebScraper`      | up to `top_k_to_scrape` URLs, concurrent           | no   |
| 4 | Validate   | `PageValidator`   | scores every scraped page (one LLM call per page)  | yes  |
| 5 | Summarize  | `Summarizer`      | only over pages **kept** by stage 4                | yes  |

Stage-by-stage details:

1. **Search** — SerpAPI primary, Google CSE fallback on error/quota.
   Cached by `{query, top_k, provider}`.
2. **Select** — The LLM picks the most promising indices from the
   SERP. Garbage / malformed reply triggers the client's retry budget;
   after exhaustion, the pipeline falls back to the first `k` results
   so the run always makes progress.
3. **Scrape** — Concurrent `httpx.AsyncClient` gated by a semaphore;
   `trafilatura` extracts main content. Network / extraction errors
   become `ScrapedPage(status=0, ...)` rather than exceptions. Cached
   by URL; zombie entries (status=200, content="") auto-heal on read.
4. **Validate** — The LLM scores each page on `relevance` and
   `trustworthiness` (both `[0, 1]`, bounds enforced by pydantic).
   Pages below either threshold are dropped.
5. **Summarize** — The LLM produces markdown with inline `[n]` citations
   anchored to **kept** pages only. Empty-kept-list (every page
   rejected) produces a graceful "no reliable sources found" summary
   rather than an error.

**Short-circuits** (no error, just skip ahead):

- Zero search results → jump directly to summarize-with-empty; no LLM
  call at all.
- Selector picks nothing → same.

All five stages log a structured line with `stage=<name>`.

---

## Install

Requires Python ≥ 3.11.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

The `a2a-sdk[http-server]` extra pulls in `starlette` + `sse-starlette`,
which the A2A ASGI app needs.

---

## Configure

Two YAML files live under `configs/` — both are **checked in** and
contain **no secrets**.

### `configs/parameters.yaml` — tunables

```yaml
search:
  top_k_results: 8
  top_k_to_scrape: 3
  serpapi_engine: google
  ...
llm:
  backend: litellm                                    # "gemini" | "litellm"
  litellm_base_url: https://litellm.llm-platform-acn.com
  model: gemini-2.5-flash
  temperature: 0.2
  ...
cache:
  enabled: true
  dir: artifacts/cache
  ...
logging:
  level: INFO
  dir: artifacts/logs
  ...
server:
  host: 127.0.0.1
  port: 8000
```

### `configs/credentials.yaml` — env-var pointers

```yaml
serpapi:
  api_key_env: "SERPAPI_API_KEY"
google_cse:
  api_key_env: "GOOGLE_CSE_API_KEY"
  cx_env:      "GOOGLE_CSE_CX"
gemini:
  api_key_env: "GEMINI_API_KEY"
  gcp_project_id: null
  gcp_location: null
litellm:
  api_key_env: "LITELLM_API_KEY"
```

The `*_env` fields name the environment variables carrying the actual
secret. This file is safe to commit.

### `.env` — actual secrets

Copy `.env.example` to `.env` and fill in real values:

```
SERPAPI_API_KEY=sk-...
GOOGLE_CSE_API_KEY=AIza...
GOOGLE_CSE_CX=...

# LLM — fill in only the backend you picked in parameters.yaml.
GEMINI_API_KEY=AIza...
LITELLM_API_KEY=sk-litellm-...
```

`.env` is gitignored. Real process environment wins over `.env` when
both set the same variable (override-off).

---

## Run

### CLI smoke (bypasses the A2A wire)

```bash
python scripts/run_local.py "what is the A2A protocol"
```

Prints the markdown summary + numbered sources to stdout, populates the
three JSONL caches, writes to `artifacts/logs/agent.log`.

### A2A server

```bash
python scripts/run_server.py
```

Serves on `http://127.0.0.1:8000` by default:

- `GET  /.well-known/agent-card.json` — the Agent Card manifest.
- `POST /` — JSON-RPC endpoint (`message/send`, `tasks/cancel`, …).
- `POST /stream` — JSON-RPC + SSE streaming endpoint.

External A2A clients send `message/send` with the query as a `TextPart`;
the server streams back `TaskStatusUpdate(working)` →
`TaskArtifactUpdate` → `TaskStatusUpdate(completed, final=true)`.

### Clear the caches

```bash
python scripts/clear_cache.py
```

Truncates `artifacts/cache/*.jsonl`. The next run will re-populate them
from live APIs.

---

## LLM backends

Two interchangeable backends live behind a single `CallFn` protocol in
`functions/llm/client.py`. Selection is a config flag — no code changes
needed to switch.

### `gemini` — direct `google-genai` SDK

```yaml
# configs/parameters.yaml
llm:
  backend: gemini
  model: gemini-2.5-flash
```

```
# .env
GEMINI_API_KEY=AIza...
```

### `litellm` — OpenAI-compatible proxy

Useful when a central gateway mediates model access (keys, quotas,
routing to multiple providers). Any LiteLLM proxy works; this repo
targets `https://litellm.llm-platform-acn.com` by default.

```yaml
# configs/parameters.yaml
llm:
  backend: litellm
  litellm_base_url: https://litellm.llm-platform-acn.com
  # The model string must match what the proxy advertises, e.g.
  #   "gemini/gemini-2.5-flash"
  #   "openai/gpt-4o-mini"
  #   "anthropic/claude-3-5-sonnet-latest"
  model: gemini-2.5-flash
```

```
# .env
LITELLM_API_KEY=sk-litellm-...
```

The client POSTs canonical OpenAI `chat/completions` to
`{litellm_base_url}/chat/completions` with
`Authorization: Bearer ${LITELLM_API_KEY}`. Retries, caching, and
JSON-output parsing are identical across backends — they all happen
above the `CallFn` seam.

Missing credentials for the selected backend raise at startup (in
`wiring.build_pipeline`), not on the first request.

---

## Deploy with Docker

A production-lean image + compose file are checked in at the repo root.

### One-command local deployment

```bash
cp .env.example .env            # fill in your keys
docker compose up --build
```

The agent will be available on `http://localhost:8000`. Verify with:

```bash
curl http://localhost:8000/.well-known/agent-card.json
python scripts/client_smoke.py "what is the Google A2A protocol?"
```

### What the image contains

- **Base**: `python:3.12-slim`.
- **Two-stage build**: `builder` stage compiles `lxml` (for
  `trafilatura`) with dev headers; the final `runtime` stage only
  carries runtime libs + the prebuilt venv.
- **Non-root user** (`app`) owns `/app`.
- **Healthcheck**: `urllib` probe against
  `/.well-known/agent-card.json` every 30 s.
- **No secrets baked in**. The image only reads env vars + bind-mounted
  config/artifacts at runtime.

### Runtime configuration

`scripts/run_server.py` honors three env-var overrides — set them in
`.env`, via `-e`, or in `docker-compose.yml`:

| Var               | Default                     | Purpose                                       |
|-------------------|-----------------------------|-----------------------------------------------|
| `A2A_HOST`        | `0.0.0.0` (in image)        | Bind address.                                 |
| `A2A_PORT`        | `8000`                      | Bind port.                                    |
| `A2A_PUBLIC_URL`  | `http://localhost:8000`     | URL advertised in the Agent Card.             |
| `A2A_HOST_PORT`   | `8000` (compose only)       | Host-side port mapped to container's 8000.    |

Set `A2A_PUBLIC_URL=https://research.example.com` when running behind
a reverse proxy so clients receive the externally reachable URL rather
than the in-container bind address.

### Persisted volumes

`docker-compose.yml` bind-mounts:

- `./artifacts/cache` → `/app/artifacts/cache` — the three JSONL caches
  survive restarts, and cache hits across container lifecycles Just Work.
- `./artifacts/logs` → `/app/artifacts/logs` — rotating log file is
  tail-able from the host (`tail -f artifacts/logs/agent.log`).

---

## Project layout

```
OnlineResearchAgent/
├── configs/
│   ├── credentials.yaml            # env-var pointers (committed, no secrets)
│   └── parameters.yaml             # tunables (top_k, thresholds, timeouts, paths)
├── prompts/
│   ├── result_selection.yaml
│   ├── validation.yaml
│   └── summarization.yaml
├── functions/
│   ├── agent/
│   │   ├── agent_card.py           # AgentCard builder
│   │   ├── server.py               # Starlette ASGI app (build_app)
│   │   ├── skills.py               # ResearchAgentExecutor
│   │   └── wiring.py               # build_pipeline(settings)
│   ├── core/
│   │   ├── cache.py                # JsonlCache
│   │   ├── scraping.py             # WebScraper
│   │   ├── search.py               # WebSearcher (SerpAPI + CSE fallback)
│   │   ├── selection.py            # ResultSelector
│   │   ├── validation.py           # PageValidator
│   │   ├── summarization.py        # Summarizer
│   │   └── research_pipeline.py    # ResearchPipeline (orchestrator)
│   ├── llm/
│   │   ├── client.py               # GeminiClient (cache-backed, retries)
│   │   └── prompts.py              # PromptTemplate + YAML loader
│   └── utils/
│       ├── config.py               # Typed Settings + load_settings
│       ├── hashing.py              # stable_hash (canonical-JSON SHA-256)
│       ├── logging.py              # configure_logging + JSON formatter
│       └── paths.py                # project path constants
├── scripts/
│   ├── run_local.py                # CLI smoke
│   ├── run_server.py               # A2A server bootstrap
│   └── clear_cache.py              # wipe JSONL caches
├── tests/                          # 116 tests, 91% coverage
├── artifacts/
│   ├── cache/                      # JSONL caches (gitignored)
│   └── logs/                       # rotating log file (gitignored)
├── development_logs/               # per-day dev notes
├── Dockerfile                      # two-stage build (builder + runtime)
├── docker-compose.yml              # one-service stack with persisted caches/logs
├── .dockerignore
├── .env.example
├── pytest.ini
└── requirements.txt
```

---

## Test

```bash
pytest                                   # 116 tests
pytest --cov=functions --cov-report=term-missing
```

All externals (SERP API, CSE API, HTTP, Gemini) are mocked. No network,
no keys required. The critical end-to-end test
(`tests/test_research_pipeline.py::test_second_run_is_fully_cached`)
proves that identical queries are served entirely from the JSONL caches.

---

## JSONL cache

Each of the three caches (search, scrape, llm) is an **append-only**
newline-delimited JSON file under `artifacts/cache/`:

```
{"key": "<sha256>", "value": {...}, "ts": "2026-04-20T12:34:56+00:00"}
{"key": "<sha256>", "value": {...}, "ts": "2026-04-20T12:34:57+00:00"}
```

- **Keys** are SHA-256 of canonical JSON of the cache payload
  (`stable_hash`).
- **Last-write-wins** on read — a `get` scans forward and returns the
  most recent record for a key.
- **Crash-safe** — writes are atomic line appends; a half-written line
  from a crashed process is recoverable (the corrupt line is skipped
  with a WARNING log).
- **Inspectable** — every layer's records are homogeneous and `jq`-able.
- **Clearable** — `scripts/clear_cache.py` truncates all three files.

---

## Logging

`configure_logging(level, log_file)` installs two handlers on the root
logger:

- `RotatingFileHandler` → `artifacts/logs/agent.log`
  (5 MiB × 3 backups by default).
- `StreamHandler(stderr)`.

Both use a single-line JSON formatter. Every module uses
`log = logging.getLogger(__name__)`; `print()` is banned.

Every pipeline stage emits an INFO line with a `stage` field, making
the log a grep-able audit trail:

```json
{"ts":"...","level":"INFO","logger":"functions.core.search","msg":"search ok","stage":"search","provider":"serpapi","count":8}
{"ts":"...","level":"INFO","logger":"functions.core.selection","msg":"selection done","stage":"select","candidates":8,"picked":3}
{"ts":"...","level":"INFO","logger":"functions.core.scraping","msg":"scrape ok","stage":"scrape","url":"https://...","chars":4821}
{"ts":"...","level":"INFO","logger":"functions.core.validation","msg":"validation done","stage":"validate","total":3,"kept":2}
{"ts":"...","level":"INFO","logger":"functions.core.summarization","msg":"summarize ok","stage":"summarize","pages":2,"chars":1834}
```

---

## Structured LLM output (pydantic)

Every JSON response from the LLM is validated against a pydantic model
*before* reaching business logic. Three wins:

1. **Schema drift fails loud.** A prompt regression that makes the model
   drop `relevance` or swap it for `relavence` raises instead of
   silently scoring every page 0.0.
2. **One retry budget covers everything.** `llm.retries` now gates
   transport failures **and** JSON parse failures **and** schema
   violations. A flaky formatter gets the same remediation as a flaky
   network — retry, eventually give up, surface `LlmCallError`.
3. **Cache self-heals.** If a cached response no longer satisfies the
   current schema (e.g. you tightened a `Field(ge=0, le=1)` bound or
   added a required field), the entry is treated as a miss; the fresh
   response shadows it via last-write-wins. No manual cache flush.

Three entry points on `GeminiClient`:

```python
await client.generate(system, user)                         # -> str
await client.generate_json(system, user)                    # -> dict[str, Any]
await client.generate_model(response_model=Foo, system, user)  # -> Foo
```

Response models live next to the prompt that shapes them:

```python
# functions/core/selection.py
class _SelectionResponse(BaseModel):
    picks: list[int]

# functions/core/validation.py
class _ValidationResponse(BaseModel):
    relevance:       float = Field(ge=0.0, le=1.0)
    trustworthiness: float = Field(ge=0.0, le=1.0)
    reason:          str = ""
```

The consumer code shrinks to `response.relevance` — no `.get(..., 0.0)`,
no `float(...)`, no clamping. Pydantic enforces the contract; the
client enforces retries; the module enforces domain rules.

---

## A2A protocol surface

The agent conforms to A2A protocol version `0.3.0`:

- **Agent Card** served at `/.well-known/agent-card.json` advertises
  `streaming: true`, one skill (`web_research`), and `text/plain` input
  / `text/markdown` output modes.
- **Execution model**: `ResearchAgentExecutor.execute(context,
  event_queue)` emits:
  1. `TaskStatusUpdateEvent(state=working, final=False)`
  2. `TaskArtifactUpdateEvent` — summary markdown + sources block as
     two `TextPart`s inside one `Artifact`.
  3. `TaskStatusUpdateEvent(state=completed, final=True)`
- **Error modes**: empty/whitespace query →
  `state=failed, final=True` (no pipeline invocation). Pipeline
  exception → same.
- **Cancellation**: `cancel()` emits `state=canceled, final=True`.

---

## Register with the agent catalog

The catalog's "Register Agent" form (URL mode) only needs the **base
URL** — it fetches `/.well-known/agent-card.json` itself and reads skills,
modes, and the JSON-RPC endpoint from the card. No manual-entry needed.

| Where the catalog runs | Where the agent runs        | Register with                         |
|------------------------|-----------------------------|---------------------------------------|
| Same host, bare metal  | `python scripts/run_server.py` | `http://localhost:8000`           |
| Same host, bare metal  | `docker compose up`         | `http://localhost:8000`               |
| Docker / Docker Compose| On the host                 | `http://host.docker.internal:8000`    |
| Docker / Docker Compose| Another container (same net)| `http://online-research-agent:8000`   |

### Make the card reflect the registered URL

The `url` field inside the Agent Card is what clients use to POST
messages — it **must** match what you registered. Override it via
`A2A_PUBLIC_URL` when running in Docker:

```yaml
# docker-compose.yml
environment:
  A2A_PUBLIC_URL: "http://host.docker.internal:8000"
```

Or on the command line:

```bash
docker run --rm -p 8000:8000 \
  -e A2A_PUBLIC_URL=http://host.docker.internal:8000 \
  --env-file .env \
  online-research-agent:latest
```

Without this override the card would advertise `http://0.0.0.0:8000`
(the container's bind address), which is not reachable from the
catalog. Verify with:

```bash
curl http://localhost:8000/.well-known/agent-card.json | jq .url
# "http://host.docker.internal:8000"
```

---

## Catalogue fit

`ResearchPipeline.run(query) -> ResearchResult` is signature-compatible
with the sibling catalogue's `executor_dict` entries — adapt by wrapping:

```python
async def web_research(query: str, **kwargs) -> str:
    settings = load_settings(...)
    pipeline = build_pipeline(settings)
    result = await pipeline.run(query)
    return result.summary_markdown
```

Both entry points (A2A skill + catalogue function) share the same
pipeline, caches, and logs.

---

## Out of scope (v1)

- Multi-turn conversations / session memory.
- Auth on the A2A server (localhost-only for now).
- TTL / eviction on the JSONL cache — `clear_cache.py` resets.
- PDF / JS-rendered page handling (trafilatura handles static HTML).

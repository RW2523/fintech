# 02 — DGX Spark environment

Everything in this build runs on one NVIDIA DGX Spark. Treat the facts below as the baseline; verify
versions with `make env` on the actual machine and record differences in `docs/PROGRESS.md`.

## 1. Hardware and OS facts that shape the build

| Fact | Consequence |
|---|---|
| GB10 Grace Blackwell superchip: 20 Arm cores (aarch64), Blackwell GPU (`sm_121`), **128 GB unified LPDDR5X** shared by CPU and GPU, ~4 TB NVMe | One memory pool for OS, containers, PostgreSQL, model weights and KV cache. Budget it (see §4.3). CPU offload gives nothing; large models load without OOM but bandwidth (~270 GB/s class) bounds decode speed. |
| DGX OS (Ubuntu 24.04 based), CUDA 13.x, Docker + NVIDIA Container Runtime pre-installed | Use containers for GPU work. Pull only **arm64 / multi-arch** images. |
| Standard PyTorch releases do not target `sm_121`; NGC `pytorch:25.10-py3` (or later) and PyTorch nightly `cu128`/`cu130` do | GPU Python work (embeddings, reranker, VLM client side) runs in an NGC-based image; CPU-only services use `python:3.12-slim` arm64. |
| Some packages lack aarch64+CUDA wheels (`flash_attn`, `mmcv`, `decord`); **PaddleOCR has no official aarch64 support** | Do not use PaddleOCR. Use Tesseract + VLM. Avoid `flash_attn` builds; vLLM images already include what they need. |
| First LLM request triggers JIT/compile (~25 s on vLLM) | `make warmup` before every demo; the gateway also warms on start. |

## 2. Host preparation (once)

```bash
uname -m                      # aarch64
nvidia-smi                    # GPU visible, CUDA 13.x
docker --version              # >= 24
docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi
# NGC login (only if pulling NGC images / NIMs): docker login nvcr.io  (user: $oauthtoken, password: NGC API key)
# Hugging Face cache on the NVMe, shared by all model containers:
mkdir -p /data/hf && export HF_HOME=/data/hf
# Node 20 LTS (arm64) for the web app; uv for Python
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Ports used (all bound to the Spark's LAN address for the demo): 8000 gateway, 8080 web, 3000 grafana,
8233 temporal-ui, 9001 minio console, 8020 llm-gateway (internal), 8100 vLLM, 11434 ollama.

## 3. Container images (all multi-arch or arm64-native)

| Role | Image | Notes |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | multi-arch |
| Redis | `redis:7-alpine` | |
| MinIO | `minio/minio:latest` + `minio/mc` | arm64 available |
| Temporal | `temporalio/auto-setup:1.25` + `temporalio/ui:2.31` (pin exact tags at build) | dev server backed by the same PostgreSQL (separate database `temporal`) |
| Observability | `otel/opentelemetry-collector-contrib`, `prom/prometheus`, `grafana/loki`, `grafana/tempo`, `grafana/grafana` | multi-arch |
| Python services (CPU) | `python:3.12-slim-bookworm` (arm64) + `tesseract-ocr`, `libgl1`, `poppler-utils` where needed | base image `docker/images/python-base/Dockerfile` |
| GPU Python (llm_gateway embeddings/rerank; ml training) | `nvcr.io/nvidia/pytorch:25.10-py3` (or newer 25.x/26.x) | includes Blackwell-capable torch; add `uv`, project deps |
| vLLM (local LLM) | `vllm/vllm-openai:cu130-nightly` at first; **pin a validated tag/digest** once one works on the Spark | OpenAI-compatible server on :8100 |
| Ollama (alternative) | native install `curl -fsSL https://ollama.com/install.sh | sh` (arm64) or `ollama/ollama` image | OpenAI-compatible on :11434 |
| Web | `node:20-alpine` build → `nginx:alpine` serve | |
| Playwright (synthetic docs, UI tests) | `mcr.microsoft.com/playwright/python:v1.4x-noble` (arm64 available) | |

## 4. Local LLM strategy

### 4.1 Routes and default model assignment

The gateway exposes routes; each route maps to a provider+model in `.env`. Defaults for the Spark:

| Route | Used by | Default local model (verify availability at build time) | Fallback (`ai-remote`) |
|---|---|---|---|
| `agent` | Council and longitudinal agents (structured JSON out) | `Qwen/Qwen3-30B-A3B` (MoE, ~3B active — fast decode) served by vLLM; FP8 or NVFP4 checkpoint if available | hosted model via `anthropic` or `openai_compatible` |
| `reasoning` | Challenger, narratives, Manager Copilot | same instance as `agent` by default; optionally a larger NVFP4 MoE (e.g. a ~120B-A12B class model) if memory allows | hosted |
| `fast` | Tier-0 narration, Member Assistant, copilots | same instance, lower `max_tokens` | hosted small |
| `vision` | document classification/extraction | `Qwen/Qwen2.5-VL-7B-Instruct` via a second vLLM (or Ollama `qwen2.5vl:7b`) | hosted vision |
| `embed` | RAG, entity resolution | `BAAI/bge-m3` (sentence-transformers, GPU) inside `llm_gateway` | same |
| `rerank` | RAG | `BAAI/bge-reranker-v2-m3` (FlagEmbedding/transformers) inside `llm_gateway` | same |

Why a MoE with a small active parameter count for `agent`: the Council fires six agents in parallel and
the demo budget is ≤ 60 s for Tier 1. Decode speed on the Spark is bandwidth-bound; a ~3B-active MoE
decodes several times faster than a dense 30–70B model at similar quality for structured extraction and
classification-style reasoning. Keep opinions short (≤ 450 output tokens) and rely on tools for content.

### 4.2 vLLM launch (compose `ai-local` profile)

```yaml
vllm:
  image: vllm/vllm-openai:cu130-nightly      # pin to a validated tag/digest in PROGRESS.md
  command: >
    --model ${LLM_AGENT_MODEL}
    --served-model-name agent
    --gpu-memory-utilization 0.55
    --max-model-len 32768
    --max-num-seqs 8
    --enable-prefix-caching
    --port 8100
  environment: [HF_HOME=/hf, HF_TOKEN=${HF_TOKEN}]
  volumes: ["/data/hf:/hf"]
  deploy: { resources: { reservations: { devices: [{ driver: nvidia, count: all, capabilities: [gpu] }] } } }
  healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8100/health"], interval: 30s, timeout: 10s, retries: 20 }
vllm_vision:
  image: vllm/vllm-openai:cu130-nightly
  command: >
    --model ${LLM_VISION_MODEL} --served-model-name vision
    --gpu-memory-utilization 0.20 --max-model-len 16384 --max-num-seqs 2 --port 8101
  ...
```

Notes: `--gpu-memory-utilization` values are fractions of the **whole** 128 GB pool because CPU and GPU share it;
0.55 + 0.20 leaves ~30 GB for PostgreSQL, services, OCR and embeddings. `--max-num-seqs 8` because the Council
issues up to 6–7 concurrent requests; measure and lower to 4–6 if per-token latency degrades.
The vLLM blog for the Spark reports ~23 tok/s decode for a 120B-A12B NVFP4 model with `--max-num-seqs 4`;
expect materially faster decode with a 3B-active MoE. Record measured tok/s in PROGRESS.md.

### 4.3 Memory budget (target)

| Consumer | Budget |
|---|---|
| OS, Docker, PostgreSQL, Redis, MinIO, Temporal, services | 20–24 GB |
| vLLM `agent` (30B-A3B FP8 ≈ 32 GB weights + KV) | ≈ 60 GB (utilisation 0.55 incl. KV) |
| vLLM `vision` (7B bf16 ≈ 16 GB + KV) | ≈ 24 GB |
| Embeddings + reranker (bge-m3, bge-reranker-v2-m3) | 4–6 GB |
| Tesseract, OpenCV, ML inference (CPU) | 4 GB |
| Headroom | ≥ 10 GB |

If memory is tight: run `vision` through Ollama with a Q4 quant, or serve one model only and route `vision`
to the hosted provider.

### 4.4 Ollama alternative (simplest path if vLLM tags misbehave on the Spark)

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl edit ollama    # Environment="OLLAMA_HOST=0.0.0.0:11434" Environment="OLLAMA_NUM_PARALLEL=6" Environment="OLLAMA_MAX_LOADED_MODELS=2" Environment="OLLAMA_KEEP_ALIVE=2h"
ollama pull qwen3:30b-a3b     # agent/reasoning/fast
ollama pull qwen2.5vl:7b      # vision
```
Set `LLM_PROVIDER_AGENT=ollama`, `LLM_BASE_URL_AGENT=http://host.docker.internal:11434/v1`. Ollama's
OpenAI-compatible endpoint supports JSON schema-constrained output (`format`), which the gateway uses.

### 4.5 Hosted provider (`ai-remote`)

`LLM_PROVIDER_AGENT=anthropic` with `ANTHROPIC_API_KEY`, or `openai_compatible` with base URL + key.
The gateway masks PII before sending and unmasks after. Use for quality comparison in the harness and as
the fallback when the Spark GPU is busy or unavailable. Both profiles must pass `make warmup` and `make harness`.

## 5. Compose profiles and `.env`

```
# docker/.env.example (excerpt)
CIO_ENV=demo
POSTGRES_PASSWORD=change-me
JWT_SECRET=change-me
MINIO_ROOT_USER=cio  MINIO_ROOT_PASSWORD=change-me-too
HF_TOKEN=
LLM_AGENT_MODEL=Qwen/Qwen3-30B-A3B
LLM_VISION_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
LLM_PROVIDER_AGENT=vllm       # vllm | ollama | anthropic | openai_compatible
LLM_BASE_URL_AGENT=http://vllm:8100/v1
LLM_PROVIDER_REASONING=vllm   LLM_BASE_URL_REASONING=http://vllm:8100/v1
LLM_PROVIDER_FAST=vllm        LLM_BASE_URL_FAST=http://vllm:8100/v1
LLM_PROVIDER_VISION=vllm      LLM_BASE_URL_VISION=http://vllm_vision:8101/v1
ANTHROPIC_API_KEY=            OPENAI_COMPATIBLE_API_KEY=
EMBED_MODEL=BAAI/bge-m3       RERANK_MODEL=BAAI/bge-reranker-v2-m3
TIER1_BUDGET_SECONDS=60  TIER2_BUDGET_SECONDS=180  TIER1_TOKEN_BUDGET=60000  TIER2_TOKEN_BUDGET=150000
```

Profiles: `core` (infra) · `services` (domain services) · `web` · `observability` · `ai-local` (vllm,
vllm_vision) · `ai-remote` (no GPU containers). `compose.spark.yaml` adds GPU reservations and the `/data/hf` mount.

## 6. arm64 dependency checklist (check before adding anything)

Known-good on aarch64: fastapi, uvicorn, pydantic v2, sqlalchemy, asyncpg, alembic, temporalio, redis,
minio, httpx, numpy, pandas, scipy, scikit-learn, lightgbm, shap, lifelines, ruptures (builds from sdist),
networkx, rapidfuzz, imagehash, Pillow, opencv-python-headless, pytesseract, pdf2image, playwright,
sentence-transformers, transformers, torch (NGC image), datamodel-code-generator, hypothesis, pytest.
Avoid: paddlepaddle/paddleocr, flash_attn (build), decord, torchcodec.

## 7. Performance targets on the Spark (record actuals in PROGRESS.md)

| Item | Target |
|---|---|
| Tier 0 case | ≤ 5 s |
| Tier 1 case (6 agents ∥ + Challenger + narratives) | p95 ≤ 60 s |
| Tier 2 case | p95 ≤ 180 s |
| Document (2 pages) OCR + VLM extraction | ≤ 30 s |
| Nightly LMI for 5,000 members | ≤ 10 min |
| `make reset` (excl. model load) | ≤ 2 min |
| `make warmup` | ≤ 90 s |

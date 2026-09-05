#!/usr/bin/env bash
# T-002 — DGX Spark environment check (docs/02_DGX_SPARK_ENVIRONMENT.md).
# Prints a table. Exit 0 when nothing is FAIL; GPU checks degrade to WARN off-Spark.
set -uo pipefail

PASS=0; WARN=0; FAIL=0
ROWS=()

row () { # name | status | detail
  ROWS+=("$1|$2|$3")
  case "$2" in PASS) PASS=$((PASS+1));; WARN) WARN=$((WARN+1));; FAIL) FAIL=$((FAIL+1));; esac
}

# --- required ports (docs/02 §2) ------------------------------------------
PORTS=(5432 6379 9000 7233 8080 3000 8000)

# --- architecture ----------------------------------------------------------
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ]; then row "architecture" PASS "$ARCH"
else row "architecture" FAIL "$ARCH (expected aarch64 — arm64-only images per CLAUDE.md §2.9)"; fi

row "kernel" PASS "$(uname -r)"

# --- docker ----------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  DV="$(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  DMAJ="${DV%%.*}"
  if [ "${DMAJ:-0}" -ge 24 ]; then row "docker" PASS "$DV"
  else row "docker" FAIL "$DV (need >= 24)"; fi
  if docker compose version >/dev/null 2>&1; then
    row "docker compose" PASS "$(docker compose version --short 2>/dev/null || echo present)"
  else row "docker compose" FAIL "not available"; fi
else
  row "docker" FAIL "not installed"
  row "docker compose" FAIL "not installed"
fi

# --- gpu -------------------------------------------------------------------
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  row "nvidia-smi" PASS "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  if [ "${SKIP_GPU_RUNTIME:-0}" = "1" ]; then
    row "nvidia container runtime" WARN "skipped (SKIP_GPU_RUNTIME=1)"
  elif timeout 180 docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
    row "nvidia container runtime" PASS "GPU visible inside a container"
  else
    row "nvidia container runtime" WARN "could not run a GPU container (image pull or runtime); needed before T-040"
  fi
else
  row "nvidia-smi" WARN "no GPU visible — ai-local profile unavailable, use ai-remote"
  row "nvidia container runtime" WARN "skipped (no GPU)"
fi

if command -v nvcc >/dev/null 2>&1; then
  row "cuda toolkit" PASS "$(nvcc --version | grep -oE 'release [0-9]+\.[0-9]+' | head -1 | cut -d' ' -f2)"
else
  row "cuda toolkit" WARN "nvcc not on PATH (containers carry their own)"
fi

# --- memory (docs/02 §4.3 budgets ~104 GB) ---------------------------------
MEM_TOTAL_GB=$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo)
MEM_AVAIL_GB=$(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo)
if [ "$MEM_AVAIL_GB" -ge 96 ]; then
  row "memory available" PASS "${MEM_AVAIL_GB} GB of ${MEM_TOTAL_GB} GB"
elif [ "$MEM_AVAIL_GB" -ge 40 ]; then
  row "memory available" WARN "${MEM_AVAIL_GB} GB of ${MEM_TOTAL_GB} GB (< 96 GB; shrink the model budget in docs/02 §4.3 or free the host)"
else
  row "memory available" FAIL "${MEM_AVAIL_GB} GB of ${MEM_TOTAL_GB} GB (too little to serve a local model)"
fi

# --- disk ------------------------------------------------------------------
DISK_GB=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')
if [ "$DISK_GB" -ge 200 ]; then row "disk free" PASS "${DISK_GB} GB"
else row "disk free" FAIL "${DISK_GB} GB (need >= 200 GB for model weights and synthetic documents)"; fi

# --- cpu -------------------------------------------------------------------
row "cpu cores" PASS "$(nproc)"

# --- toolchain -------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then row "uv" PASS "$(uv --version | awk '{print $2}')"
else row "uv" FAIL "not installed — curl -LsSf https://astral.sh/uv/install.sh | sh"; fi

if command -v node >/dev/null 2>&1; then
  NMAJ="$(node --version | tr -dc '0-9.' | cut -d. -f1)"
  if [ "${NMAJ:-0}" -ge 20 ]; then row "node" PASS "$(node --version)"
  else row "node" WARN "$(node --version) (apps/web expects >= 20 LTS)"; fi
else row "node" WARN "not installed — needed from T-046"; fi

# --- ports -----------------------------------------------------------------
for p in "${PORTS[@]}"; do
  if ss -ltnH 2>/dev/null | grep -qE "[:.]${p}[[:space:]]"; then
    OWNER="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -m1 ":${p}->" | awk '{print $1}')"
    row "port ${p}" WARN "in use${OWNER:+ by ${OWNER}} — remap in docker/.env or stop the holder"
  else
    row "port ${p}" PASS "free"
  fi
done

# --- HF cache --------------------------------------------------------------
if [ -d "${HF_HOME:-/data/hf}" ]; then row "hugging face cache" PASS "${HF_HOME:-/data/hf}"
else row "hugging face cache" WARN "${HF_HOME:-/data/hf} missing — mkdir -p and export HF_HOME before pulling weights"; fi

# --- render ----------------------------------------------------------------
printf '\n  %-26s %-6s %s\n' "CHECK" "STATUS" "DETAIL"
printf '  %-26s %-6s %s\n' "--------------------------" "------" "----------------------------------------"
for r in "${ROWS[@]}"; do
  IFS='|' read -r n s d <<< "$r"
  case "$s" in
    PASS) c=$'\033[32m';; WARN) c=$'\033[33m';; FAIL) c=$'\033[31m';;
  esac
  printf '  %-26s %b%-6s\033[0m %s\n' "$n" "$c" "$s" "$d"
done
printf '\n  %d pass · %d warn · %d fail\n\n' "$PASS" "$WARN" "$FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo "  environment not ready — resolve the FAIL rows above" >&2
  exit 1
fi
exit 0

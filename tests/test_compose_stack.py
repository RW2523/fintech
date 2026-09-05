"""T-003 — the Compact-profile compose stack matches the spec.

Structural checks only; liveness is verified by scripts/verify_phase.sh P0.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yaml").read_text())
SPARK = yaml.safe_load((ROOT / "docker" / "compose.spark.yaml").read_text())
SVCS: dict = COMPOSE["services"]

# docs/01 §4 — service -> port on the compose network
SERVICE_PORTS = {
    "gateway": 8000,
    "application": 8001,
    "document": 8002,
    "member_intelligence": 8003,
    "policy": 8004,
    "feature": 8005,
    "risk": 8006,
    "fraud": 8007,
    "lmi": 8008,
    "committee": 8009,
    "core_stub": 8010,
    "agent_runtime": 8011,
    "decision": 8012,
    "execution": 8013,
    "notification": 8014,
    "audit": 8015,
    "governance": 8016,
    "llm_gateway": 8020,
}

CORE = ["postgres", "redis", "minio", "minio-init", "temporal", "temporal-ui"]
OBSERVABILITY = ["otel-collector", "prometheus", "loki", "tempo", "grafana"]

# CLAUDE.md §2.8 — Compact profile only.
FORBIDDEN = ["kafka", "zookeeper", "kubernetes", "feast", "kserve", "opensearch", "elasticsearch"]


def profiles(name: str) -> list[str]:
    return SVCS[name].get("profiles", [])


def test_core_profile_has_every_datastore() -> None:
    for svc in CORE:
        assert svc in SVCS, f"{svc} missing from compose"
        assert "core" in profiles(svc), f"{svc} is not in the core profile"


def test_observability_profile_is_complete() -> None:
    for svc in OBSERVABILITY:
        assert svc in SVCS, f"{svc} missing from compose"
        assert "observability" in profiles(svc), f"{svc} is not in the observability profile"


def test_every_domain_service_is_present_on_its_port() -> None:
    for svc, port in SERVICE_PORTS.items():
        assert svc in SVCS, f"{svc} missing from compose"
        assert "services" in profiles(svc), f"{svc} is not in the services profile"
        assert SVCS[svc]["environment"]["SERVICE_PORT"] == str(port), f"{svc} on the wrong port"


def test_every_long_lived_container_has_a_healthcheck() -> None:
    """One documented exception: the distroless collector is probed externally."""
    exempt = {"minio-init", "otel-collector"}
    missing = [
        name
        for name in CORE + OBSERVABILITY + list(SERVICE_PORTS)
        if name not in exempt and "healthcheck" not in SVCS[name]
    ]
    assert not missing, f"containers without a healthcheck: {missing}"


def test_collector_health_port_is_published_for_the_external_probe() -> None:
    ports = " ".join(SVCS["otel-collector"].get("ports", []))
    assert "13133" in ports, "collector health_check extension is not reachable from the host"


def test_data_lives_in_named_volumes() -> None:
    expected = {"pg_data", "redis_data", "minio_data", "prom_data", "loki_data", "tempo_data", "grafana_data"}
    assert expected <= set(COMPOSE["volumes"]), "missing named volumes"


def test_ai_profiles_exist_and_carry_no_gpu_reservation_in_the_base_file() -> None:
    for svc in ("vllm", "vllm_vision"):
        assert "ai-local" in profiles(svc), f"{svc} is not in the ai-local profile"
        assert "deploy" not in SVCS[svc], f"{svc} must take its GPU reservation from compose.spark.yaml"


def test_spark_overlay_reserves_the_gpu_for_the_model_containers() -> None:
    for svc in ("vllm", "vllm_vision", "llm_gateway"):
        res = SPARK["services"][svc]["deploy"]["resources"]["reservations"]["devices"][0]
        assert res["driver"] == "nvidia", f"{svc} has no nvidia reservation"


def test_compact_profile_forbids_scale_profile_infrastructure() -> None:
    blob = (ROOT / "docker" / "compose.yaml").read_text().lower()
    found = [f for f in FORBIDDEN if f in blob]
    assert not found, f"CLAUDE.md §2.8 forbids these in the Compact profile: {found}"


def test_env_example_covers_every_variable_compose_reads() -> None:
    import re

    text = (ROOT / "docker" / "compose.yaml").read_text()
    referenced = set(re.findall(r"\$\{([A-Z0-9_]+)", text))
    declared = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / "docker" / ".env.example").read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = referenced - declared
    assert not missing, f"docker/.env.example does not declare: {sorted(missing)}"

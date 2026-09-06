"""Agent bundles: prompt, tools, config, version (docs/06 §1).

An agent is a directory, not a class. Everything that changes what it does --
the words of its prompt, the tools it may call, the route it runs on -- lives
in files, and the version is a hash of all of them. That is what makes an
opinion reconstructable: the record names an `agent_version`, and that version
pins the exact prompt and grants that produced it.

Changing a prompt changes the version. There is no way to edit an agent
without the record showing it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = ["Bundle", "ToolGrant", "agents_root", "list_bundles", "load_bundle"]

ROOT = Path(__file__).resolve().parent

#: Families the runtime knows. A bundle outside them is a configuration error
#: rather than something to guess about.
FAMILIES = ("council", "longitudinal", "copilot", "governance")

#: Routes an agent may run on (docs/02 §4.1).
ROUTES = ("agent", "reasoning", "fast", "vision")

#: docs/06 §1 — the version is the first sixteen hex characters of the hash.
VERSION_LENGTH = 16


@dataclass(frozen=True, slots=True)
class ToolGrant:
    """One tool an agent may call, and how often."""

    name: str
    version: str = "1.0"
    max_calls: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "max_calls": self.max_calls}


@dataclass(frozen=True, slots=True)
class Bundle:
    """One agent, as it exists on disk."""

    agent_id: str
    family: str
    route: str
    prompt: str
    tools: tuple[ToolGrant, ...]
    output_schema: str
    max_output_tokens: int = 700
    temperature: float = 0.1
    path: Path = ROOT
    #: The model id the route resolves to, folded into the version so that
    #: swapping the model behind a route produces a new agent version rather
    #: than silently changing what an existing one means.
    route_model: str = ""

    @property
    def agent_version(self) -> str:
        return version_of(self.prompt, self.tools, self.config_digest, self.route_model)

    @property
    def config_digest(self) -> str:
        return _sha(
            yaml.safe_dump(
                {
                    "family": self.family,
                    "route": self.route,
                    "output_schema": self.output_schema,
                    "max_output_tokens": self.max_output_tokens,
                    "temperature": self.temperature,
                },
                sort_keys=True,
            )
        )

    @property
    def call_budget(self) -> int:
        """docs/06 §1 — the loop may make at most this many tool calls."""
        return sum(grant.max_calls for grant in self.tools)

    def grant(self, name: str) -> ToolGrant | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "family": self.family,
            "route": self.route,
            "output_schema": self.output_schema,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "tools": [t.as_dict() for t in self.tools],
            "call_budget": self.call_budget,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def version_of(prompt: str, tools: tuple[ToolGrant, ...], config_digest: str, route_model: str) -> str:
    """docs/06 §1 — sha256 over prompt, tools, config and the route's model."""
    material = "\x1f".join(
        [
            prompt.strip(),
            yaml.safe_dump([t.as_dict() for t in tools], sort_keys=True),
            config_digest,
            route_model,
        ]
    )
    return _sha(material)[:VERSION_LENGTH]


def agents_root() -> Path:
    return ROOT


def load_bundle(agent_id: str, *, root: Path | None = None, route_model: str = "") -> Bundle:
    """Read one agent's bundle from disk."""
    directory = (root or ROOT) / agent_id
    if not directory.is_dir():
        raise FileNotFoundError(f"no agent bundle at {directory}")

    prompt_file = directory / "prompt.md"
    config_file = directory / "config.yaml"
    tools_file = directory / "tools.yaml"
    for required in (prompt_file, config_file):
        if not required.is_file():
            raise FileNotFoundError(f"{agent_id} is missing {required.name}")

    config = yaml.safe_load(config_file.read_text()) or {}
    family = str(config.get("family", ""))
    route = str(config.get("route", ""))
    if family not in FAMILIES:
        raise ValueError(f"{agent_id} declares family {family!r}; expected one of {', '.join(FAMILIES)}")
    if route not in ROUTES:
        raise ValueError(f"{agent_id} declares route {route!r}; expected one of {', '.join(ROUTES)}")

    grants: list[ToolGrant] = []
    if tools_file.is_file():
        for entry in (yaml.safe_load(tools_file.read_text()) or {}).get("tools") or []:
            grants.append(
                ToolGrant(
                    name=str(entry["name"]),
                    version=str(entry.get("version", "1.0")),
                    max_calls=int(entry.get("max_calls", 1)),
                )
            )

    return Bundle(
        agent_id=agent_id,
        family=family,
        route=route,
        prompt=prompt_file.read_text(),
        tools=tuple(grants),
        output_schema=str(config.get("output_schema", "agent_opinion/1.3")),
        max_output_tokens=int(config.get("max_output_tokens", 700)),
        temperature=float(config.get("temperature", 0.1)),
        path=directory,
        route_model=route_model,
    )


def list_bundles(root: Path | None = None) -> list[str]:
    directory = root or ROOT
    return sorted(p.name for p in directory.iterdir() if p.is_dir() and (p / "prompt.md").is_file())


@lru_cache(maxsize=32)
def cached_bundle(agent_id: str, route_model: str = "") -> Bundle:
    return load_bundle(agent_id, route_model=route_model)

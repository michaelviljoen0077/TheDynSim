"""LLM adapter: provider-agnostic candidate generation (FR18, Story 3.3).

OllamaProvider is the sole production tier (purely local, schema-constrained
decoding + bounded repair retries). ReplayProvider replays recorded fixtures so
the whole loop runs offline in CI. Nothing downstream may know which provider
produced a candidate.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "hypothesis": {"type": "string"},
        "expected_outcome": {"type": "string"},
        "lineage_parent": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "plugin_source": {"type": "string"},
    },
    "required": ["analysis", "hypothesis", "expected_outcome", "confidence", "plugin_source"],
}


@dataclass
class CandidateProposal:
    analysis: str
    hypothesis: str
    expected_outcome: str
    confidence: float
    plugin_source: str
    lineage_parent: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0


class GenerationError(Exception):
    pass


class LLMProvider(Protocol):
    name: str

    def generate(self, prompt: str) -> tuple[CandidateProposal, Usage]: ...


def _parse_proposal(text: str) -> CandidateProposal:
    data = json.loads(text)
    missing = [k for k in PROPOSAL_SCHEMA["required"] if k not in data]
    if missing:
        raise GenerationError(f"proposal missing keys: {missing}")
    return CandidateProposal(
        analysis=str(data["analysis"]),
        hypothesis=str(data["hypothesis"]),
        expected_outcome=str(data["expected_outcome"]),
        confidence=float(data["confidence"]),
        plugin_source=str(data["plugin_source"]),
        lineage_parent=data.get("lineage_parent"),
    )


class OllamaProvider:
    """Local GPU inference via Ollama /api/chat with JSON-schema-constrained output."""

    def __init__(self, model: str = "qwen3-coder:30b",
                 base_url: str = "http://localhost:11434",
                 timeout_s: float = 300.0, retries: int = 2,
                 keep_alive: str = "30m") -> None:
        self.name = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.keep_alive = keep_alive

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as r:
                tags = json.loads(r.read())
            return any(m["name"].startswith(self.model) for m in tags.get("models", []))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return False

    def generate(self, prompt: str) -> tuple[CandidateProposal, Usage]:
        usage = Usage()
        last_error = ""
        for _attempt in range(1 + self.retries):
            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "format": PROPOSAL_SCHEMA,
                "stream": False,
                # num_ctx set explicitly: the governor prompt (API doc + report +
                # recall + live sources) can exceed Ollama's default window, which
                # would truncate SILENTLY and lobotomize generation
                "options": {"temperature": 0.7, "num_predict": 4096, "num_ctx": 16384},
                "keep_alive": self.keep_alive,
            }
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read())
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
                last_error = f"transport: {e}"
                continue
            usage.latency_s += time.perf_counter() - t0
            usage.tokens_in += int(data.get("prompt_eval_count", 0))
            usage.tokens_out += int(data.get("eval_count", 0))
            try:
                return _parse_proposal(data["message"]["content"]), usage
            except (GenerationError, json.JSONDecodeError, KeyError) as e:
                last_error = f"malformed: {e}"
                continue
        raise GenerationError(f"generation failed after {1 + self.retries} attempts: {last_error}")


class ReplayProvider:
    """Replays recorded proposals — the test/CI tier. Never touches the network."""

    def __init__(self, proposals: list[dict] | None = None,
                 fixture_path: str | Path | None = None) -> None:
        self.name = "replay"
        if fixture_path is not None:
            proposals = json.loads(Path(fixture_path).read_text())
        self._queue: list[dict] = list(proposals or [])

    def generate(self, prompt: str) -> tuple[CandidateProposal, Usage]:
        if not self._queue:
            raise GenerationError("replay fixture exhausted")
        raw = self._queue.pop(0)
        return _parse_proposal(json.dumps(raw)), Usage(tokens_in=len(prompt) // 4)

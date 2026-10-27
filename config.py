"""Paths, topics and secret loading for the paper -> agent-environment pipeline."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
SURVEY_NAME = "agent-rsi-papers-2025-10-26_2026-10-25.csv"


def _find_survey() -> Path:
    for base in (ROOT / "paper-radar", REPO_ROOT / "paper-radar"):
        candidate = base / "survey" / SURVEY_NAME
        if candidate.exists():
            return candidate
    return ROOT / "paper-radar" / "survey" / SURVEY_NAME


SURVEY_CSV = _find_survey()
ENVS_DIR = ROOT / "envs"
KAROTTE_DIR = ROOT / "karotte_env"
RUNS_DIR = ROOT / "runs"
DATA_DIR = ROOT / "data"
DASHBOARD_HTML = ROOT / "dashboard.html"
DASHBOARD_JSON = ROOT / "dashboard_data.json"

API_BASE = os.environ.get("PAPER_ENV_API_BASE", "https://tierflow.cn/v1")
MODEL = os.environ.get("PAPER_ENV_MODEL", "tierflow_pro")
EFFORT = os.environ.get("PAPER_ENV_EFFORT", "max")

MCODE_ROOT = Path(os.environ.get("PAPER_ENV_MCODE_ROOT", REPO_ROOT / "minimax-code"))
MCODE_MODEL = os.environ.get(
    "PAPER_ENV_MCODE_MODEL", "custom_provider:tierflow/tierflow_pro"
)
MCODE_EFFORT = os.environ.get("PAPER_ENV_MCODE_EFFORT", "max")
MCODE_PERMISSION = os.environ.get("PAPER_ENV_MCODE_PERMISSION", "full")
MCODE_DATA_DIR = ROOT / ".mcode-data"

TOPICS: dict[str, str] = {
    "agent-harness": "Agent Harness / 智能体外壳",
    "rsi": "RSI / 递归自我改进",
    "agent-environment": "Agent Environment / 智能体环境",
    "post-training": "后训练 / Post-training",
    "rl-infra": "RL Infra / 强化学习基础设施",
    "inference-infra": "Inference Infra / 推理基础设施",
}

TOPIC_ACCENTS: dict[str, str] = {
    "agent-harness": "#5eead4",
    "rsi": "#c084fc",
    "agent-environment": "#7dd3fc",
    "post-training": "#fbbf24",
    "rl-infra": "#fb7185",
    "inference-infra": "#a3e635",
}


@dataclass
class Settings:
    api_base: str = API_BASE
    model: str = MODEL
    effort: str = EFFORT
    api_key: str = field(default="", repr=False)
    timeout: float = 600.0
    max_retries: int = 3

    def has_key(self) -> bool:
        return bool(self.api_key)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _read_mcode_key() -> str:
    """Read the tierflow key from the MiniMax Code config, if present.

    The pipeline never writes this secret anywhere; it is read at runtime so a
    single local config can serve both MCode and the planner.
    """
    path = Path.home() / ".minimax" / "config.yaml"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_tierflow = False
    for line in lines:
        if re.match(r"^  tierflow:\s*$", line):
            in_tierflow = True
            continue
        if in_tierflow:
            if re.match(r"^  \S", line):
                break
            match = re.match(r"^\s+apiKey:\s*(\S+)\s*$", line)
            if match:
                return match.group(1)
    return ""


def load_settings() -> Settings:
    dotenv = _read_dotenv(ROOT / ".env.local")
    key = (
        os.environ.get("PAPER_ENV_API_KEY")
        or os.environ.get("TIERFLOW_API_KEY")
        or dotenv.get("PAPER_ENV_API_KEY", "")
        or dotenv.get("TIERFLOW_API_KEY", "")
        or _read_mcode_key()
    )
    return Settings(api_key=key)


def topic_name(topic_id: str) -> str:
    return TOPICS.get(topic_id, topic_id)

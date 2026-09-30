"""The one door every side effect must pass through. Default: closed."""
import json
from pathlib import Path

from .paths import config_dir


class ExecutionBlocked(RuntimeError):
    pass


def load(path=None) -> dict:
    return json.loads(Path(path or config_dir() / "execution.json").read_text(encoding="utf-8"))


def require(target: str, path=None) -> None:
    """Raise unless the master switch is on, someone approved it, AND this specific target is enabled."""
    cfg = load(path)
    if not cfg.get("enabled"):
        raise ExecutionBlocked(f"execution is disabled (dry-run mode) - refused: {target}")
    if not cfg.get("approved_by") or not cfg.get("approved_at"):
        raise ExecutionBlocked(f"execution enabled but not approved by a person - refused: {target}")
    if not cfg.get("targets", {}).get(target):
        raise ExecutionBlocked(f"target '{target}' is not enabled - refused")

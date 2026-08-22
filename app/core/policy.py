from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REQUIRED_SCORE_KEYS = {
    "regime", "mtf", "structure", "liquidity", "zone", "volume",
    "momentum", "volatility", "timing", "setup_quality",
}


@lru_cache(maxsize=8)
def load_policy(path: str = "configs/config.v1.yaml") -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("policy must be a mapping")
    weights = payload.get("score_weights", {})
    if set(weights) != REQUIRED_SCORE_KEYS:
        raise ValueError("score_weights must contain exactly the ten canonical factors")
    total = sum((Decimal(str(value)) for value in weights.values()), Decimal("0"))
    if total != Decimal("100"):
        raise ValueError("score_weights must sum to 100")
    for section in ("regime", "risk"):
        if not isinstance(payload.get(section), dict):
            raise ValueError(f"missing policy section: {section}")
    return payload

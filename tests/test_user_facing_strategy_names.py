import json
import re
from pathlib import Path


def load_registry() -> dict:
    return json.loads(Path("strategies.json").read_text(encoding="utf-8"))


def test_user_facing_strategy_names_are_canonical_russian_names():
    registry = load_registry()
    policy = registry["policy"]

    assert policy["user_facing_names"] == "Только поле name_ru"
    assert policy["show_technical_strategy_ids_to_user"] is False

    for strategy in registry["strategies"]:
        name = strategy["name_ru"].strip()
        assert name
        assert not re.match(r"^(?:S|T|BT)\d", name)
        assert "High Pressure" not in name
        assert "live-" not in name.lower()


def test_all_active_strategies_have_unique_user_facing_names():
    registry = load_registry()
    active_names = [
        strategy["name_ru"].strip()
        for strategy in registry["strategies"]
        if strategy.get("status") != "ARCHIVED"
        and (
            strategy.get("scanner_enabled") is True
            or strategy.get("mining_enabled") is True
            or strategy.get("research_destination") == "mining"
        )
    ]

    assert len(active_names) == len(set(active_names))

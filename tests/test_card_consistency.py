"""Static consistency tests for the bundled card."""
from pathlib import Path
import re

CARD = Path("custom_components/periodical/frontend/periodical-card.js")


def test_rendered_entity_keys_exist_in_map() -> None:
    source = CARD.read_text(encoding="utf-8")
    block = re.search(r"const ENTITY_MAP = \{(.*?)\n\};", source, re.S)
    assert block is not None
    mapped = set(re.findall(r"^\s*([a-zA-Z0-9_]+):", block.group(1), re.M))
    used = set(re.findall(r"this\._(?:state|val|num|attr)\('([^']+)'", source))
    assert used <= mapped


def test_no_removed_compound_entities() -> None:
    source = CARD.read_text(encoding="utf-8")
    for removed in ("pay_summary", "vacation_summary", "api_issues"):
        assert removed not in source

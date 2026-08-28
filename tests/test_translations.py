"""Translation completeness tests."""
import json
from pathlib import Path

BASE = Path("custom_components/periodical")


def test_swedish_entity_keys_match_english() -> None:
    english = json.loads((BASE / "translations/en.json").read_text(encoding="utf-8"))
    swedish = json.loads((BASE / "translations/sv.json").read_text(encoding="utf-8"))
    assert swedish["entity"]["sensor"].keys() == english["entity"]["sensor"].keys()
    assert swedish["entity"]["binary_sensor"].keys() == english["entity"]["binary_sensor"].keys()

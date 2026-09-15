import json
from pathlib import Path

from aqt import gui_hooks

from anki_plugin_interference_recorder import hooks
from anki_plugin_interference_recorder.search_dialog import page_count, page_slice

ADDON_ROOT = (
    Path(__file__).parents[1] / "src" / "anki_plugin_interference_recorder"
)


def test_register_hooks_is_idempotent() -> None:
    registration_count = gui_hooks.main_window_did_init.count()

    hooks.register_hooks()
    hooks.register_hooks()

    assert gui_hooks.main_window_did_init.count() == registration_count


def test_config_contains_only_supported_options() -> None:
    config = json.loads((ADDON_ROOT / "config.json").read_text(encoding="utf-8"))

    assert config == {"shortcut": "0", "decay": 0.9}


def test_manifest_uses_product_name() -> None:
    manifest = json.loads((ADDON_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "Interference Recorder"


def test_local_addon_metadata_uses_product_name() -> None:
    metadata = json.loads((ADDON_ROOT / "meta.json").read_text(encoding="utf-8"))

    assert metadata["name"] == "Interference Recorder"


def test_search_results_are_paginated_without_a_total_limit() -> None:
    card_ids = list(range(1, 251))

    assert page_count(len(card_ids)) == 3
    assert list(page_slice(card_ids, 0)) == list(range(1, 101))
    assert list(page_slice(card_ids, 1)) == list(range(101, 201))
    assert list(page_slice(card_ids, 2)) == list(range(201, 251))

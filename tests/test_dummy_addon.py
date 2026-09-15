import json
from pathlib import Path

from aqt import gui_hooks

from anki_plugin_interference_recorder import hooks

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

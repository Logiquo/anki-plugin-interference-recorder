from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from pathlib import Path

import aqt  # noqa: F401 - initializes Anki's generated Python modules
import pytest
from anki.cards import CardId
from anki.collection import Collection
from anki.decks import DeckConfigId

from anki_plugin_interference_recorder import storage
from anki_plugin_interference_recorder.storage import (
    DATA_NAME,
    EVENTS_FIELD,
    InterferenceEvent,
    StorageError,
    append_event,
    ensure_storage,
    get_or_create_writer_id,
    grade_and_record,
    iter_interference_events,
    new_event,
)


@pytest.fixture
def collection(tmp_path: Path) -> Iterator[Collection]:
    col = Collection(str(tmp_path / "collection.anki2"))
    try:
        yield col
    finally:
        col.close()


def _add_basic_card(collection: Collection, front: str) -> CardId:
    notetype = collection.models.by_name("Basic")
    deck_id = collection.decks.id_for_name("Default")
    assert notetype is not None
    assert deck_id is not None

    note = collection.new_note(notetype)
    note["Front"] = front
    note["Back"] = f"{front} back"
    collection.add_note(note, deck_id)
    card_ids = collection.card_ids_of_note(note.id)
    assert len(card_ids) == 1
    return card_ids[0]


def test_writer_id_is_stable_and_invalid_content_is_not_replaced(tmp_path: Path) -> None:
    user_files = tmp_path / "user_files"
    first = get_or_create_writer_id(user_files)
    second = get_or_create_writer_id(user_files)

    assert first == second
    assert str(uuid.UUID(first)) == first

    (user_files / "writer_id").write_text("not-a-uuid", encoding="utf-8")
    with pytest.raises(StorageError, match="Invalid writer ID"):
        get_or_create_writer_id(user_files)
    assert (user_files / "writer_id").read_text(encoding="utf-8") == "not-a-uuid"


def test_storage_uses_dedicated_deck_config_without_changing_default(
    collection: Collection,
) -> None:
    default_before = copy.deepcopy(collection.decks.get_config(DeckConfigId(1)))

    deck_id, notetype = ensure_storage(collection)
    second_deck_id, second_notetype = ensure_storage(collection)

    assert deck_id == second_deck_id
    assert notetype["id"] == second_notetype["id"]
    assert collection.decks.name(deck_id) == DATA_NAME
    assert collection.decks.config_dict_for_deck_id(deck_id)["name"] == DATA_NAME
    assert collection.decks.get_config(DeckConfigId(1)) == default_before
    assert [field["name"] for field in notetype["flds"]] == ["Id", "Events"]


def test_parts_roll_over_and_cards_are_suspended(
    collection: Collection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_storage(collection)
    writer_id = get_or_create_writer_id(tmp_path / "user_files")
    monkeypatch.setattr(storage, "EVENTS_PER_PART", 2)

    for target in (2, 3, 4):
        undo_target = collection.add_custom_undo_entry("append test")
        append_event(
            collection,
            new_event(CardId(1), CardId(target)),
            writer_id,
            undo_target,
        )

    note_ids = collection.find_notes(f'note:"{DATA_NAME}"')
    notes = sorted((collection.get_note(note_id) for note_id in note_ids), key=lambda n: n["Id"])
    assert [note["Id"].rsplit("-", 1)[1] for note in notes] == ["0001", "0002"]
    assert [len(storage._decode_events(note[EVENTS_FIELD], note["Id"])) for note in notes] == [2, 1]
    for note in notes:
        card_ids = collection.card_ids_of_note(note.id)
        assert len(card_ids) == 1
        assert collection.get_card(card_ids[0]).queue == -1


def test_grade_event_and_undo_are_one_operation(
    collection: Collection, tmp_path: Path
) -> None:
    source = _add_basic_card(collection, "A")
    target = _add_basic_card(collection, "B")
    ensure_storage(collection)
    writer_id = get_or_create_writer_id(tmp_path / "user_files")
    event = new_event(source, target)

    grade_and_record(collection, event, writer_id)

    assert len(collection.get_review_logs(source)) == 1
    assert len(collection.get_review_logs(target)) == 1
    assert iter_interference_events(collection) == [event]
    assert collection.undo_status().undo == "Record Interference"

    undone = collection.undo()

    assert undone.operation == "Record Interference"
    assert len(collection.get_review_logs(source)) == 0
    assert len(collection.get_review_logs(target)) == 0
    assert iter_interference_events(collection) == []


def test_undo_restores_an_existing_shard(collection: Collection, tmp_path: Path) -> None:
    source = _add_basic_card(collection, "A")
    first_target = _add_basic_card(collection, "B")
    second_target = _add_basic_card(collection, "C")
    ensure_storage(collection)
    writer_id = get_or_create_writer_id(tmp_path / "user_files")
    first = new_event(source, first_target)
    second = new_event(source, second_target)

    grade_and_record(collection, first, writer_id)
    grade_and_record(collection, second, writer_id)
    assert iter_interference_events(collection) == [first, second]

    collection.undo()

    assert iter_interference_events(collection) == [first]
    assert len(collection.get_review_logs(source)) == 1
    assert len(collection.get_review_logs(first_target)) == 1
    assert len(collection.get_review_logs(second_target)) == 0


def test_storage_failure_rolls_back_both_grades(
    collection: Collection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _add_basic_card(collection, "A")
    target = _add_basic_card(collection, "B")
    ensure_storage(collection)
    writer_id = get_or_create_writer_id(tmp_path / "user_files")
    event = new_event(source, target)

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise StorageError("simulated write failure")

    monkeypatch.setattr(storage, "append_event", fail_append)

    with pytest.raises(StorageError, match="simulated write failure"):
        grade_and_record(collection, event, writer_id)

    assert len(collection.get_review_logs(source)) == 0
    assert len(collection.get_review_logs(target)) == 0
    assert iter_interference_events(collection) == []


def test_event_validation_requires_direction_and_timezone() -> None:
    with pytest.raises(StorageError, match="different cards"):
        InterferenceEvent(str(uuid.uuid4()), 1, 1, "2026-09-15T12:00:00-04:00").validate()
    with pytest.raises(StorageError, match="UTC offset"):
        InterferenceEvent(str(uuid.uuid4()), 1, 2, "2026-09-15T12:00:00").validate()

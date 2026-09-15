from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import aqt  # noqa: F401 - initializes Anki's generated Python modules
import pytest
from anki.cards import CardId
from anki.collection import Collection
from anki.decks import DeckConfigId

from anki_plugin_interference_recorder import storage
from anki_plugin_interference_recorder.maintenance import (
    CLEAN_UNDO_NAME,
    execute_cleanup,
    scan_missing_card_records,
)
from anki_plugin_interference_recorder.storage import (
    DATA_NAME,
    EVENTS_FIELD,
    InterferenceEvent,
    StorageError,
    append_event,
    decode_events,
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


def _add_shard(
    collection: Collection, shard_id: str, events: list[InterferenceEvent]
) -> None:
    deck_id, notetype = ensure_storage(collection)
    note = collection.new_note(notetype)
    note["Id"] = shard_id
    note[EVENTS_FIELD] = json.dumps(
        [event.to_dict() for event in events], separators=(",", ":")
    )
    collection.add_note(note, deck_id)


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
    assert [len(decode_events(note[EVENTS_FIELD], note["Id"])) for note in notes] == [2, 1]
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


def test_cleanup_removes_missing_events_deletes_empty_shards_and_undoes_once(
    collection: Collection,
) -> None:
    source = _add_basic_card(collection, "A")
    target = _add_basic_card(collection, "B")
    valid = new_event(source, target)
    missing_from_mixed = new_event(source, CardId(9_000_000_001))
    missing_from_empty = new_event(CardId(9_000_000_002), target)
    writer_id = str(uuid.uuid4())
    mixed_id = f"{writer_id}-part-0001"
    empty_id = f"{writer_id}-part-0003"
    _add_shard(collection, mixed_id, [valid, missing_from_mixed])
    _add_shard(collection, empty_id, [missing_from_empty])

    plan = scan_missing_card_records(collection)

    assert plan.scanned_shards == 2
    assert plan.total_events == 3
    assert plan.missing_events == 2
    assert plan.affected_shards == 2
    assert plan.empty_shards == 1

    execute_cleanup(collection, plan)

    remaining_notes = [
        collection.get_note(note_id)
        for note_id in collection.find_notes(f'note:"{DATA_NAME}"')
    ]
    assert [note["Id"] for note in remaining_notes] == [mixed_id]
    assert iter_interference_events(collection) == [valid]
    assert collection.undo_status().undo == CLEAN_UNDO_NAME

    undone = collection.undo()

    assert undone.operation == CLEAN_UNDO_NAME
    restored_notes = {
        collection.get_note(note_id)["Id"]: collection.get_note(note_id)
        for note_id in collection.find_notes(f'note:"{DATA_NAME}"')
    }
    assert set(restored_notes) == {mixed_id, empty_id}
    assert decode_events(restored_notes[mixed_id][EVENTS_FIELD], mixed_id) == [
        valid,
        missing_from_mixed,
    ]
    assert decode_events(restored_notes[empty_id][EVENTS_FIELD], empty_id) == [
        missing_from_empty
    ]


def test_cleanup_rejects_a_stale_scan_without_overwriting_changes(
    collection: Collection,
) -> None:
    source = _add_basic_card(collection, "A")
    missing = new_event(source, CardId(9_000_000_003))
    shard_id = f"{uuid.uuid4()}-part-0001"
    _add_shard(collection, shard_id, [missing])
    plan = scan_missing_card_records(collection)
    note_id = collection.find_notes(f'note:"{DATA_NAME}"')[0]
    note = collection.get_note(note_id)
    note[EVENTS_FIELD] = "[]"
    collection.update_note(note)

    with pytest.raises(StorageError, match="changed after the scan"):
        execute_cleanup(collection, plan)

    assert collection.get_note(note_id)[EVENTS_FIELD] == "[]"


def test_cleanup_scan_preserves_corrupt_shards(collection: Collection) -> None:
    deck_id, notetype = ensure_storage(collection)
    note = collection.new_note(notetype)
    note["Id"] = f"{uuid.uuid4()}-part-0001"
    note[EVENTS_FIELD] = "not json"
    collection.add_note(note, deck_id)

    with pytest.raises(StorageError, match=note["Id"]):
        scan_missing_card_records(collection)

    assert collection.get_note(note.id)[EVENTS_FIELD] == "not json"

"""Creation of exact-card Anki filtered decks from graph selections."""

from __future__ import annotations

from dataclasses import dataclass

from anki.cards import CardId
from anki.collection import Collection, OpChanges, SearchNode
from anki.decks import DeckId, FilteredDeckConfig
from anki.errors import DeletedError, NotFoundError

from .storage import StorageError


@dataclass(frozen=True)
class FilteredDeckResult:
    changes: OpChanges
    deck_id: DeckId
    deck_name: str
    requested: int
    added: int


def create_filtered_deck(
    collection: Collection,
    card_ids: list[int],
    custom_name: str,
) -> FilteredDeckResult:
    """Create and build a real filtered deck containing the requested cards."""
    requested_ids = sorted(set(card_ids))
    if not requested_ids:
        raise StorageError("No cards were selected for the filtered deck.")

    existing_ids: list[int] = []
    for card_id in requested_ids:
        try:
            collection.get_card(CardId(card_id))
        except (NotFoundError, DeletedError):
            continue
        existing_ids.append(card_id)
    if not existing_ids:
        raise StorageError("None of the selected cards still exist.")

    search = "cid:" + ",".join(str(card_id) for card_id in existing_ids)
    matched = {int(card_id) for card_id in collection.find_cards(search)}
    if matched != set(existing_ids):
        raise StorageError("Anki's exact Card ID search did not match the requested cards.")

    name = custom_name.strip()
    if name and collection.decks.id_for_name(name) is not None:
        raise StorageError(f'A deck named "{name}" already exists.')

    deck = collection.sched.get_or_create_filtered_deck(deck_id=DeckId(0))
    if name:
        deck.name = name
    deck.config.reschedule = True
    deck.allow_empty = False
    del deck.config.search_terms[:]
    deck.config.search_terms.append(
        FilteredDeckConfig.SearchTerm(
            search=search,
            limit=len(existing_ids),
            order=FilteredDeckConfig.SearchTerm.Order.DUE,
        )
    )
    result = collection.sched.add_or_update_filtered_deck(deck)
    deck_id = DeckId(result.id)
    deck_search = collection.build_search_string(SearchNode(deck=deck.name))
    added = len(collection.find_cards(deck_search))
    return FilteredDeckResult(
        changes=result.changes,
        deck_id=deck_id,
        deck_name=deck.name,
        requested=len(requested_ids),
        added=added,
    )

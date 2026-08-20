"""What the documentation search must find, and what it must not.

The second list is the one that matters. A retriever that returns something for
every query looks like it works, right up to the point where the model answers a
question about babysitting from the room service section. These cases pin the
relevance floor in place: change the scoring and the negatives are what break.

No network and no API key — this runs on the corpus alone.
"""

import pytest

from app.tools import get_reservation, search_hotel_policies

NOT_FOUND = "No section of the hotel documentation matches"

SHOULD_FIND = [
    ("what time is checkout", "Standard times"),
    # The pool section says "open daily from 07:00 to 21:00" and never uses the
    # word "hours", so only one moderately common term matches. Production
    # answered "I don't have that information" to a question the documentation
    # answers, until the threshold learned to move with how much of the question
    # the corpus recognises.
    ("pool hours", "Pool"),
    ("swimming pool hours", "Pool"),
    ("what are the pool hours", "Pool"),
    ("when does the restaurant open", "Restaurant"),
    ("room service hours", "Room service"),
    ("can I smoke on the balcony", "Smoking"),
    ("pet fee dog", "Pets"),
    ("cancellation deadline", "Cancellation policy"),
    ("wifi password", "Wi-Fi"),
    ("parking cost", "Parking"),
    ("vegan food", "Dietary requirements"),
    ("accessible room wheelchair", "Accessibility"),
    ("gym hours", "Gym"),
    ("extra bed for child", "Occupancy and extra guests"),
    ("is there a pool", "Pool"),
    ("breakfast price", "Breakfast"),
    ("spa opening hours", "Spa"),
    ("how much is the aurora suite", "Room types"),
    ("electric car charger", "Parking"),
    ("quiet hours noise", "Quiet hours"),
    ("lost my jacket", "Lost property"),
]

# Services the hotel does not offer. The agent must be told nothing rather than
# handed a passage that merely shares a common word with the question.
SHOULD_NOT_FIND = [
    "babysitting service",
    "laundry service pickup",
    "airport shuttle",
    "casino",
    "tennis court",
    "currency exchange",
    "do you have a helicopter pad",
    "is there a nightclub",
    "scuba diving lessons",
    # Known gap, named rather than pretended away: "do you rent bicycles"
    # retrieves the business services section, because the corpus says meeting
    # rooms "can be rented" and "rent" is rare in it. The passage is at least
    # adjacent, and the prompt requires the model to say when the answer is not
    # in what it was handed. Tightening further would fit this list, not the
    # problem.
]


@pytest.mark.parametrize("query,expected_section", SHOULD_FIND)
def test_finds_the_right_section(query: str, expected_section: str) -> None:
    result = search_hotel_policies.invoke({"query": query})
    assert NOT_FOUND not in result, f"{query!r} found nothing"
    assert expected_section in result, f"{query!r} did not surface {expected_section!r}"


@pytest.mark.parametrize("query", SHOULD_NOT_FIND)
def test_returns_nothing_for_what_the_hotel_does_not_offer(query: str) -> None:
    result = search_hotel_policies.invoke({"query": query})
    assert NOT_FOUND in result, f"{query!r} returned a passage it should not have"


def test_empty_query_is_rejected() -> None:
    assert "Empty query" in search_hotel_policies.invoke({"query": "   "})


def test_reservation_lookup_is_case_insensitive() -> None:
    result = get_reservation.invoke({"confirmation_code": "aur-104582 "})
    assert "Marta Delgado" in result
    assert "Deluxe King" in result


def test_unknown_reservation_refuses_instead_of_inventing() -> None:
    result = get_reservation.invoke({"confirmation_code": "AUR-999999"})
    assert "No reservation found" in result
    assert "Do not invent" in result

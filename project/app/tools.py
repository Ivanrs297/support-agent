"""The two tools the agent can call.

STEPS 9 and 10 — see README §9 and §10.

They are deliberately different in kind. `search_hotel_policies` is fuzzy: it
returns prose and the model has to interpret it. `get_reservation` is exact: it
returns structured fields or nothing at all. A real support agent mixes both, and
they fail in different ways — the first returns something irrelevant, the second
returns nothing and the model must not invent a booking to fill the gap.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import tool

from .config import settings

DATA_DIR = Path(__file__).parent / "data"
KB_DIR = DATA_DIR / "kb"

# Words carrying no signal for retrieval. Small on purpose: an aggressive stop
# list drops terms that matter here, like "pet" or "bed".
STOPWORDS = frozenset(
    """a an and are as at be by can do does for from has have how i if in is it
    its me my of on or our that the their there they this to was we what when
    where which who will with you your""".split()
)


@dataclass(frozen=True)
class Passage:
    """One `##` section of one knowledge base file."""

    source: str
    title: str
    body: str

    def render(self) -> str:
        return f"[{self.source} — {self.title}]\n{self.body}"


# Longest first, so that "policies" loses "ies" rather than "s".
SUFFIXES = ("ing", "ies", "es", "ed", "s", "y", "e")


def _normalize(word: str) -> str:
    """Strip one suffix, so related forms of a word collapse to one token.

    STEP 9.1
    A guest asks "can I smoke on the balcony"; the documentation says "smoking"
    and "balconies". Without this they share no token at all.

    The stems do not have to be linguistically correct. The same rule runs over
    the query and over the corpus, so wrong-but-identical is all it needs to be.
    Keep a minimum stem length of 3 characters, or "does" becomes "d".
    """
    raise NotImplementedError("STEP 9.1 — see README §9")


def _tokenize(text: str) -> set[str]:
    """Content words, plus every adjacent pair joined together.

    STEP 9.2
    Lowercase, split on non-alphanumerics, drop STOPWORDS, normalize the rest.

    Then add the joined pairs: for the words [a, b, c] also emit "ab" and "bc",
    normalized. That is what makes "checkout" find "check-out" and vice versa.
    Hyphenated and compound spellings are everywhere in hotel documentation and
    a guest will use whichever one they think of first.
    """
    raise NotImplementedError("STEP 9.2 — see README §9")


def _load_passages() -> list[Passage]:
    """Split every knowledge base file into its `##` sections.

    STEP 8.1
    Sections are the right unit of retrieval: small enough that a match is
    specific, large enough to answer a question on their own without stitching
    fragments back together.

    `source` is the filename with hyphens turned into spaces, `title` is the
    heading text, `body` is everything until the next heading. Raise if the
    corpus is empty — an agent silently retrieving from nothing looks exactly
    like an agent answering from memory.
    """
    raise NotImplementedError("STEP 8.1 — see README §8")


def _load_reservations() -> dict[str, dict]:
    """Read data/reservations.json, keyed by confirmation code.

    STEP 10.1
    """
    raise NotImplementedError("STEP 10.1 — see README §10")


def _build_idf(passages: list[Passage]) -> dict[str, float]:
    """How much signal each term carries, from how rare it is in the corpus.

    STEP 9.3
    Count the passages each term appears in — presence, not frequency, so a word
    repeated ten times in one section does not outrank a word in ten sections.
    Then map each term to log(1 + total / (1 + count)).

    The +1s are not decoration: without them a term appearing in every passage
    divides by its own count and a term appearing in none divides by zero.
    """
    raise NotImplementedError("STEP 9.3 — see README §9")


# Loaded once at import. The whole corpus is a few KB — reading it per request
# would be pointless I/O, and caching it costs nothing worth measuring.
PASSAGES = _load_passages()
RESERVATIONS = _load_reservations()
IDF = _build_idf(PASSAGES)

# STEP 9.5 — the relevance floor.
#
# Below this, a match is one common word and nothing more. It has to be
# calibrated against this corpus rather than guessed: measure what a term
# appearing in a single passage scores, and what one appearing in a quarter of
# them scores, then put the floor between them.
#
# Raise it and the agent starts saying "I don't know" about things it does know.
# Lower it and it starts answering from passages that merely share a word with
# the question. README §9 walks through both failures with real queries.
MIN_RELEVANCE = 0.0  # TODO: replace with a measured value


def _score(query_tokens: set[str], passage: Passage) -> tuple[float, float]:
    """Sum the inverse document frequency of the query terms a passage matches.

    STEP 9.4
    Counting matched terms is not enough. "babysitting service" and "vegan food"
    both match exactly one word of the corpus, but the first should return
    nothing and the second should return the dining section. What separates them
    is how common the matched word is: "service" appears all over the
    documentation and carries almost no signal, "vegan" appears once and carries
    all of it.

    Return two numbers, and keep them apart:

    - `rank` orders the results, and weights a title match by 3 — a section
      titled "Pets" is about pets in a way a passing mention is not.
    - `relevance` is the raw IDF of the matched terms, with no title boost, and
      it is what the floor above is compared against.

    Collapse them into one and "babysitting service" clears the floor purely by
    matching the word "services" in a heading.
    """
    raise NotImplementedError("STEP 9.4 — see README §9")


@tool
def search_hotel_policies(query: str) -> str:
    """Search Hotel Aurora's guest documentation and return matching sections.

    Covers room types and rates, reservations and cancellation, check-in and
    check-out, amenities, dining, and hotel policies such as pets, smoking,
    parking and accessibility.

    Args:
        query: What to look for, in English, as keywords rather than a full
            sentence. For example "cancellation deadline" or "pet fee".
    """
    # STEP 9.6
    #
    # This docstring is not documentation. It is the description the model reads
    # when it decides whether to call this tool, so it is prompt engineering
    # wearing a docstring's clothes. Leave it alone.
    #
    # What you write:
    #
    # 1. Tokenize the query. An empty result means an empty query — say so.
    #
    # 2. Decide the threshold for THIS query. A fixed floor cannot work, and
    #    finding out why is the point of this step. Count how many content words
    #    of the query the corpus has ever seen. A word appearing nowhere in the
    #    documentation is the strongest evidence there is that the guest is
    #    asking about something the hotel does not document.
    #
    #    A query made entirely of known vocabulary only has to match something.
    #    A query where unknown words are the majority has to clear MIN_RELEVANCE
    #    on the rest.
    #
    #    README §9 shows the two queries that force this rule — "pool hours" and
    #    "babysitting service" — and why no single threshold separates them.
    #
    # 3. Score every passage, sort by rank, filter by relevance, and only then
    #    truncate to settings.max_doc_results. Filtering after truncating drops
    #    a relevant passage ranked fourth behind three that fell below the floor.
    #
    # 4. No hits is a result, not an error. Return a sentence that tells the
    #    model to admit ignorance and offer a human, rather than an empty string
    #    it will fill in itself.
    raise NotImplementedError("STEP 9.6 — see README §9")


@tool
def get_reservation(confirmation_code: str) -> str:
    """Look up a Hotel Aurora reservation by its confirmation code.

    Args:
        confirmation_code: The code given at booking, in the format AUR-104582.
    """
    # STEP 10.2
    #
    # Normalize the code — strip it and upper-case it — then look it up.
    #
    # A miss returns prose, not None: say the code was not found, give the two
    # ordinary reasons (mistyped, booked under another code), and tell the model
    # in as many words not to invent reservation details. A tool that returns
    # nothing leaves the model to decide what nothing means.
    #
    # A hit returns JSON. The model is better at reading structure than prose
    # when the answer is a date and a room number.
    raise NotImplementedError("STEP 10.2 — see README §10")


TOOLS = [search_hotel_policies, get_reservation]

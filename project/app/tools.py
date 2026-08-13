"""The two tools the agent can call.

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


# Longest first: "policies" must lose "ies", not "s".
SUFFIXES = ("ing", "ies", "es", "ed", "s", "y", "e")


def _normalize(word: str) -> str:
    """Strip one suffix, so related forms of a word collapse to one token.

    A guest asks "can I smoke on the balcony"; the documentation says "smoking"
    and "balconies". Without this they share no token at all.

    The stems are not linguistically correct — "smoking" and "smoke" both become
    "smok", "policy" becomes "polic". That does not matter: the same rule runs
    over the query and over the corpus, so wrong-but-identical is all it needs to
    be. Proper stemming means a dependency, and this is 4 lines.
    """
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _tokenize(text: str) -> set[str]:
    """Content words, plus every adjacent pair joined together.

    The joined pairs are what make "checkout" find "check-out" and vice versa.
    Hyphenated and compound spellings are everywhere in hotel documentation, and
    a guest will use whichever one they think of first.
    """
    raw = re.findall(r"[a-z0-9]+", text.lower())
    tokens = {_normalize(w) for w in raw if w not in STOPWORDS}
    tokens |= {_normalize(a + b) for a, b in zip(raw, raw[1:])}
    return tokens


def _load_passages() -> list[Passage]:
    """Split every knowledge base file into its `##` sections.

    Sections are the right unit: small enough that a match is specific, large
    enough to answer a question on their own without stitching fragments back
    together.
    """
    passages: list[Passage] = []
    for path in sorted(KB_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for chunk in text.split("\n## ")[1:]:
            title, _, body = chunk.partition("\n")
            passages.append(
                Passage(
                    source=path.stem.replace("-", " "),
                    title=title.strip(),
                    body=body.strip(),
                )
            )
    if not passages:
        raise RuntimeError(f"No knowledge base content found in {KB_DIR}")
    return passages


def _load_reservations() -> dict[str, dict]:
    return json.loads((DATA_DIR / "reservations.json").read_text(encoding="utf-8"))


def _build_idf(passages: list[Passage]) -> dict[str, float]:
    """How much signal each term carries, from how rare it is in the corpus."""
    total = len(passages)
    frequency: Counter[str] = Counter()
    for passage in passages:
        frequency.update(_tokenize(passage.title) | _tokenize(passage.body))
    return {
        term: math.log(1 + total / (1 + count)) for term, count in frequency.items()
    }


# Loaded once at import. The whole corpus is a few KB — reading it per request
# would be pointless I/O, and caching it costs nothing worth measuring.
PASSAGES = _load_passages()
RESERVATIONS = _load_reservations()
IDF = _build_idf(PASSAGES)

# Below this, a match is one common word and nothing more. Calibrated against
# this corpus: a term appearing in a single passage scores about 2.7, one
# appearing in a quarter of them about 1.5. Raise it and the agent starts saying
# "I don't know" about things it does know; lower it and it starts answering
# from passages that merely share a word with the question.
MIN_RELEVANCE = 2.0


def _score(query_tokens: set[str], passage: Passage) -> tuple[float, float]:
    """Sum the inverse document frequency of the query terms a passage matches.

    Counting matched terms is not enough. "babysitting service" and "vegan food"
    both match exactly one word of the corpus, but the first should return
    nothing and the second should return the dining section. What separates them
    is how common the matched word is: "service" appears all over the
    documentation and carries almost no signal, "vegan" appears once and carries
    all of it.

    That is IDF, and on 28 passages it is a dictionary and a logarithm rather
    than a dependency.

    Two numbers come back, and keeping them apart matters. Relevance is the raw
    IDF of the matched terms; ranking multiplies title matches by three, because
    a section titled "Pets" is about pets in a way a passing mention is not. If
    the title boost fed the relevance threshold too, "babysitting service" would
    clear it purely by matching the word "services" in a heading.
    """
    title_tokens = _tokenize(passage.title)
    body_tokens = _tokenize(passage.body)
    relevance = 0.0
    rank = 0.0
    for token in query_tokens:
        in_title = token in title_tokens
        if not (in_title or token in body_tokens):
            continue
        weight = IDF.get(token, 0.0)
        relevance += weight
        rank += weight * (3 if in_title else 1)
    return rank, relevance


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
    tokens = _tokenize(query)
    if not tokens:
        return "Empty query. Provide keywords describing what to look for."

    # "Is there a pool?" carries one content word. Holding it to the same
    # evidence threshold as a multi-word query rejects it, because "pool" turns
    # up in four sections and its IDF is correspondingly low. A single-term
    # question is not ambiguous about what it wants, so any match will do.
    content_words = {
        _normalize(w)
        for w in re.findall(r"[a-z0-9]+", query.lower())
        if w not in STOPWORDS
    }
    threshold = MIN_RELEVANCE if len(content_words) > 1 else 0.0

    scored = sorted(
        ((*_score(tokens, p), p) for p in PASSAGES), key=lambda x: x[0], reverse=True
    )
    # Filter before truncating: a relevant passage ranked fourth should still be
    # returned when the three above it fell below the threshold.
    hits = [p for _, relevance, p in scored if relevance > threshold][
        : settings.max_doc_results
    ]

    if not hits:
        return (
            f"No section of the hotel documentation matches '{query}'. "
            "Do not guess an answer: tell the guest this is not something you "
            "have information about, and offer to pass them to a colleague."
        )
    return "\n\n---\n\n".join(p.render() for p in hits)


@tool
def get_reservation(confirmation_code: str) -> str:
    """Look up a Hotel Aurora reservation by its confirmation code.

    Args:
        confirmation_code: The code given at booking, in the format AUR-104582.
    """
    code = confirmation_code.strip().upper()
    reservation = RESERVATIONS.get(code)
    if reservation is None:
        return (
            f"No reservation found with code {code}. The code may be mistyped, or "
            "the booking may have been made under a different one. Do not invent "
            "reservation details: ask the guest to check the code on their "
            "confirmation email."
        )
    return json.dumps({"confirmation_code": code, **reservation}, indent=2)


TOOLS = [search_hotel_policies, get_reservation]

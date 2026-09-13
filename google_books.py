"""
StoryStrand - google_books.py

Metadata lookups against Google Books AND OpenLibrary, plus three features:

  1. The lazy-loading pattern: a trigger button that searches for any
     book missing metadata/cover art, rendering 0.6-opacity ghost
     placeholders while it works and swapping in the real card as data
     arrives. Tries Google Books first, then OpenLibrary for whatever
     fields are still missing, instead of stopping at a single source.

  2. "Weave My Strand" series discovery: a trigger button that checks
     every series in the library against Google Books AND OpenLibrary
     and surfaces any volumes that don't appear to be on the shelf yet,
     as ghost cards the user can add with one click.

  3. FIX: fetch_missing_metadata_parallel() - fetches metadata for a
     batch of books concurrently (up to 4 at a time) instead of one at
     a time, the same pattern the old app used (data.py's
     fetch_missing_metadata_parallel). Used by main.py right after an
     import finishes, so a big import doesn't sit there fetching
     metadata sequentially for minutes.

Both/all are self-contained here - nothing in this file rewrites the
rest of main.py, it only returns ready-to-place ft.Control objects or
plain data.
"""

from __future__ import annotations
import re
import difflib
import threading
import requests
import flet as ft
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from models import Book, Library, normalize_title_key

GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

# "#2", "Book 2", "Vol. 2" inside a title - used to guess a discovered
# volume's position in its series.
SERIES_INDEX_PATTERN = re.compile(
    r"#\s*(\d+(?:\.\d+)?)|\bBook\s+(\d+(?:\.\d+)?)|\bVol(?:ume)?\.?\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

OPENLIBRARY_SEARCH_ENDPOINT = "https://openlibrary.org/search.json"
# OpenLibrary throttles/blocks requests with no User-Agent much more
# aggressively than ones that identify the app.
OPENLIBRARY_HEADERS = {"User-Agent": "StoryStrand/1.0 (personal book library app; contact: n/a)"}

STOPWORDS = {"the", "a", "an", "of", "and", "&"}


def _normalize_words(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS]


def _title_is_plausible_match(wanted_title: str, candidate_title: str) -> bool:
    """A title/author search result isn't a unique key like an ISBN
    lookup is -- reject results that don't actually look like the
    book that was searched for."""
    wt = " ".join(_normalize_words(wanted_title))
    ct = " ".join(_normalize_words(candidate_title))
    if not wt or not ct:
        return False
    ratio = difflib.SequenceMatcher(None, wt, ct).ratio()
    return ratio >= 0.6 or wt in ct or ct in wt


def _author_matches(wanted_author: str, candidate_authors: List[str]) -> bool:
    """Rejects results that share a title but are by a different
    author entirely. If we don't know the wanted author, nothing to
    check against, so it passes."""
    wanted_words = _normalize_words(wanted_author)
    if not wanted_words or (wanted_author or "").strip().lower() == "unknown author":
        return True
    wanted_norm = " ".join(wanted_words)
    wanted_last = wanted_words[-1]
    for cand in candidate_authors or []:
        cand_norm = " ".join(_normalize_words(cand))
        if not cand_norm:
            continue
        if cand_norm in wanted_norm or wanted_norm in cand_norm:
            return True
        if wanted_last in cand_norm.split():
            return True
    return False


# ---------------- single-book metadata lookup (multi-source) ----------------

def search_google_books(title: str, author: str = "") -> Optional[dict]:
    """One lookup against the Google Books API. Returns a plain dict of
    whatever fields we could find, or None if nothing matched."""
    query_parts = [f'intitle:{title}']
    if author and author != "Unknown Author":
        query_parts.append(f'inauthor:{author}')
    params = {"q": " ".join(query_parts), "maxResults": 1}

    try:
        resp = requests.get(GOOGLE_BOOKS_ENDPOINT, params=params, timeout=10)

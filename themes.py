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
import time
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
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    items = data.get("items") or []
    if not items:
        return None

    info = items[0].get("volumeInfo", {})
    image_links = info.get("imageLinks", {})
    industry_ids = {i.get("type"): i.get("identifier") for i in info.get("industryIdentifiers", [])}

    return {
        "title": info.get("title", title),
        "authors": ", ".join(info.get("authors", [])) or author,
        "description": info.get("description", ""),
        "page_count": info.get("pageCount"),
        "categories": ", ".join(info.get("categories", [])),
        "published": info.get("publishedDate", ""),
        "publisher": info.get("publisher", ""),
        "cover_url": image_links.get("thumbnail") or image_links.get("smallThumbnail") or "",
        "isbn": industry_ids.get("ISBN_13") or industry_ids.get("ISBN_10") or "",
    }


def _search_openlibrary_single(title: str, author: str = "") -> Optional[dict]:
    """A second, independent single-book lookup used to fill gaps
    Google Books leaves behind (or to find the book at all, if Google
    Books simply doesn't have it). Checked for a plausible title/author
    match before being trusted, since OpenLibrary's search endpoint
    isn't a unique-key lookup either."""
    params = {
        "title": title,
        "fields": "title,author_name,cover_i,publisher,first_publish_year,number_of_pages_median,isbn",
        "limit": 1,
    }
    if author and author != "Unknown Author":
        params["author"] = author

    try:
        resp = requests.get(OPENLIBRARY_SEARCH_ENDPOINT, params=params, timeout=10, headers=OPENLIBRARY_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    docs = data.get("docs") or []
    if not docs:
        return None
    doc = docs[0]

    if not _title_is_plausible_match(title, doc.get("title", "")):
        return None
    if not _author_matches(author, doc.get("author_name", [])):
        return None

    cover_id = doc.get("cover_i")
    isbn_list = doc.get("isbn") or []
    return {
        "title": doc.get("title", title),
        "authors": ", ".join(doc.get("author_name", [])) or author,
        "description": "",  # OpenLibrary's search endpoint doesn't return descriptions
        "page_count": doc.get("number_of_pages_median"),
        "categories": "",  # subject list needs a separate /works/ lookup; skipped on this fast path
        "published": str(doc.get("first_publish_year", "")) if doc.get("first_publish_year") else "",
        "publisher": (doc.get("publisher") or [""])[0],
        "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else "",
        "isbn": isbn_list[0] if isbn_list else "",
    }


_MERGE_FIELDS = ["title", "authors", "description", "page_count", "categories",
                 "published", "publisher", "cover_url", "isbn"]


def _merge_metadata(*sources: Optional[dict]) -> Optional[dict]:
    """Fills one result from multiple source dicts in priority order:
    the first source wins for a given field, but a field missing from
    an earlier source can still be filled by a later one, instead of
    the whole lookup failing because one source came up short."""
    merged: dict = {}
    any_found = False
    for source in sources:
        if not source:
            continue
        any_found = True
        for field in _MERGE_FIELDS:
            if not merged.get(field) and source.get(field):
                merged[field] = source[field]
    return merged if any_found else None


def search_book_metadata(title: str, author: str = "") -> Optional[dict]:
    """The lookup used by 'Find Missing Book Info': tries Google Books
    first, then OpenLibrary for whatever fields are still missing (or
    for the whole book, if Google Books had nothing at all) -- rather
    than stopping at a single source and calling a book unfindable
    just because one API doesn't carry it."""
    google_result = search_google_books(title, author)
    still_missing = (
        google_result is None
        or not all([
            google_result.get("description"), google_result.get("page_count"),
            google_result.get("cover_url"), google_result.get("publisher"),
            google_result.get("published"),
        ])
    )
    openlibrary_result = _search_openlibrary_single(title, author) if still_missing else None
    return _merge_metadata(google_result, openlibrary_result)


def apply_metadata(book: Book, meta: dict) -> bool:
    """Fill in only the fields the book is missing; never clobber
    something the user already entered manually. Returns True if any
    field actually changed, so callers can report a real "found" count
    instead of just "we ran a lookup"."""
    changed = False
    if not book.description and meta.get("description"):
        book.description = meta["description"]
        changed = True
    if not book.page_count and meta.get("page_count"):
        book.page_count = meta["page_count"]
        changed = True
    if not book.cover_url and meta.get("cover_url"):
        book.cover_url = meta["cover_url"]
        changed = True
    if not book.genre and meta.get("categories"):
        book.genre = meta["categories"]
        changed = True
    if not book.published and meta.get("published"):
        book.published = meta["published"]
        changed = True
    if not book.publisher and meta.get("publisher"):
        book.publisher = meta["publisher"]
        changed = True
    if not book.isbn and meta.get("isbn"):
        book.isbn = meta["isbn"]
        changed = True
    book.metadata_fetched = True
    return changed


# ---------------- FIX: parallel batch metadata fetch ----------------

def fetch_missing_metadata_parallel(
    books: List[Book],
    want_cover: bool = True,
    want_description: bool = True,
    want_genre: bool = True,
    want_pubinfo: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    max_workers: int = 4,
) -> dict:
    """FIX: fetches metadata for a list of books CONCURRENTLY (up to
    max_workers lookups in flight at once) instead of one at a time.
    This is the same pattern the old app used (data.py's
    fetch_missing_metadata_parallel) -- 4 workers is deliberately
    conservative, since OpenLibrary in particular starts throttling or
    dropping requests well before 8 concurrent connections.

    Mutates each Book in place via apply_metadata(). Does NOT call
    library.save() -- the caller decides when to persist, since this
    may run against books that aren't part of a Library yet.

    want_cover / want_description / want_genre / want_pubinfo mirror
    the old app's Import-tab checkboxes: set any of them False to skip
    filling that field even if a source has it.

    Returns {"found": N, "total": N} where "found" counts books that
    got at least one new field filled in.
    """
    total = len(books)
    if total == 0:
        return {"found": 0, "total": 0}

    found = 0
    done = 0
    workers = min(max_workers, max(1, total))

    def _filtered_meta(meta: Optional[dict]) -> Optional[dict]:
        if not meta:
            return None
        meta = dict(meta)
        if not want_cover:
            meta["cover_url"] = ""
        if not want_description:
            meta["description"] = ""
        if not want_genre:
            meta["categories"] = ""
        if not want_pubinfo:
            meta["publisher"] = ""
            meta["published"] = ""
            meta["page_count"] = None
        return meta

    def work(book: Book):
        meta = search_book_metadata(book.title, book.author)
        return book, _filtered_meta(meta)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(work, b) for b in books]
        for future in as_completed(futures):
            book, meta = future.result()
            got = False
            if meta:
                got = apply_metadata(book, meta)
            if got:
                found += 1
            done += 1
            if on_progress:
                on_progress(done, total)

    return {"found": found, "total": total}


# ---------------- series discovery ("Weave My Strand") ----------------
#
# Two independent, imperfect sources are combined here rather than
# relying on Google Books alone: neither has a real "series" field, so
# each is queried by series name (+ author) and the results are pooled,
# deduped, and filtered together. A volume only needs to turn up in
# ONE of the two to be surfaced - this trades a bit more noise (caught
# by the filters below) for a lot fewer missed hits, since a given
# series is often better indexed in one source than the other.

def _series_words_present(series_name: str, haystack: str) -> bool:
    """True only if every significant word of the series name shows up
    somewhere in the candidate title/subtitle - catches "Stormlight
    Archive, Book Four" for a series named "The Stormlight Archive"
    (a plain substring check would miss it), while still rejecting
    results that don't reference the series at all."""
    series_words = set(_normalize_words(series_name))
    if not series_words:
        return False
    return series_words.issubset(set(_normalize_words(haystack)))


def _guess_series_index(title: str) -> Optional[float]:
    match = SERIES_INDEX_PATTERN.search(title)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None

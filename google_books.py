"""
StoryStrand - google_books.py

Metadata lookups against the Google Books API, plus two features:

  1. The lazy-loading pattern: a trigger button that searches Google
     Books in the background for any book missing metadata/cover art,
     rendering 0.6-opacity ghost placeholders while it works and
     swapping in the real card as data arrives.

  2. "Weave My Strand" series discovery: a trigger button that checks
     every series in the library against Google Books and surfaces any
     volumes that don't appear to be on the shelf yet, as ghost cards
     the user can add with one click.

Both are self-contained here - nothing in this file rewrites the rest
of main.py, it only returns ready-to-place ft.Control objects.
"""

from __future__ import annotations
import re
import threading
import requests
import flet as ft
from typing import Callable, List, Optional

from models import Book, Library, normalize_title_key

GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

# "#2", "Book 2", "Vol. 2" inside a title - used to guess a discovered
# volume's position in its series.
SERIES_INDEX_PATTERN = re.compile(
    r"#\s*(\d+(?:\.\d+)?)|\bBook\s+(\d+(?:\.\d+)?)|\bVol(?:ume)?\.?\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


# ---------------- raw API call (single-book lookup) ----------------

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


def apply_metadata(book: Book, meta: dict) -> None:
    """Fill in only the fields the book is missing; never clobber
    something the user already entered manually."""
    if not book.description and meta.get("description"):
        book.description = meta["description"]
    if not book.page_count and meta.get("page_count"):
        book.page_count = meta["page_count"]
    if not book.cover_url and meta.get("cover_url"):
        book.cover_url = meta["cover_url"]
    if not book.genre and meta.get("categories"):
        book.genre = meta["categories"]
    if not book.published and meta.get("published"):
        book.published = meta["published"]
    if not book.publisher and meta.get("publisher"):
        book.publisher = meta["publisher"]
    if not book.isbn and meta.get("isbn"):
        book.isbn = meta["isbn"]
    book.metadata_fetched = True


# ---------------- series discovery ("Weave My Strand") ----------------
#
# Two independent, imperfect sources are combined here rather than
# relying on Google Books alone: neither has a real "series" field, so
# each is queried by series name (+ author) and the results are pooled,
# deduped, and filtered together. A volume only needs to turn up in
# ONE of the two to be surfaced - this trades a bit more noise (caught
# by the filters below) for a lot fewer missed hits, since a given
# series is often better indexed in one source than the other.

OPENLIBRARY_SEARCH_ENDPOINT = "https://openlibrary.org/search.json"
# OpenLibrary throttles/blocks requests with no User-Agent much more
# aggressively than ones that identify the app.
OPENLIBRARY_HEADERS = {"User-Agent": "StoryStrand/1.0 (personal book library app; contact: n/a)"}

STOPWORDS = {"the", "a", "an", "of", "and", "&"}


def _normalize_words(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS]


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


def _author_matches(wanted_author: str, candidate_authors: List[str]) -> bool:
    """Rejects results that share the series name in their title but
    are by a different author entirely - the single biggest source of
    false positives when a series name is generic or reused. If we
    don't actually know the series' author, nothing to check against."""
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


def _guess_series_index(title: str) -> Optional[float]:
    match = SERIES_INDEX_PATTERN.search(title)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _search_google_books_raw(series_name: str, author: str = "", max_results: int = 40) -> List[dict]:
    """Common-shape candidates {title, subtitle, authors, cover_url}
    from Google Books. Returns [] on any network failure rather than
    raising, so one source failing doesn't block the other."""
    query_parts = [f'"{series_name}"']
    if author and author != "Unknown Author":
        query_parts.append(f'inauthor:{author}')
    params = {"q": " ".join(query_parts), "maxResults": max_results}

    try:
        resp = requests.get(GOOGLE_BOOKS_ENDPOINT, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    results = []
    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        title = (info.get("title") or "").strip()
        if not title:
            continue
        image_links = info.get("imageLinks", {})
        results.append({
            "title": title,
            "subtitle": info.get("subtitle") or "",
            "authors": info.get("authors", []) or [],
            "cover_url": image_links.get("thumbnail") or image_links.get("smallThumbnail") or "",
        })
    return results


def _search_openlibrary_raw(series_name: str, author: str = "", max_results: int = 40) -> List[dict]:
    """Same common shape as _search_google_books_raw, sourced from
    OpenLibrary's search API instead - a genuinely separate index, so
    it turns up different gaps than Google Books does."""
    params = {
        "q": series_name,
        "fields": "title,subtitle,author_name,cover_i",
        "limit": max_results,
    }
    if author and author != "Unknown Author":
        params["author"] = author

    try:
        resp = requests.get(
            OPENLIBRARY_SEARCH_ENDPOINT, params=params, timeout=10, headers=OPENLIBRARY_HEADERS
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    results = []
    for doc in data.get("docs", []):
        title = (doc.get("title") or "").strip()
        if not title:
            continue
        cover_id = doc.get("cover_i")
        results.append({
            "title": title,
            "subtitle": doc.get("subtitle") or "",
            "authors": doc.get("author_name", []) or [],
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else "",
        })
    return results


def find_missing_series_volumes(series_name: str, owned_titles: List[str], author: str = "") -> List[dict]:
    """Best-effort discovery: pool results from Google Books AND
    OpenLibrary for a series name, then keep only volumes that (a)
    aren't already owned, (b) actually reference every significant
    word of the series name, and (c) are credited to a matching
    author when we know one. A volume from either source can pass;
    duplicates (including different editions of the same book) are
    collapsed via normalize_title_key before the result is returned.
    """
    candidates = _search_google_books_raw(series_name, author) + _search_openlibrary_raw(series_name, author)

    owned_keys = {normalize_title_key(t) for t in owned_titles}
    seen_keys = set()
    missing = []

    for cand in candidates:
        title = cand["title"]
        key = normalize_title_key(title)
        if key in owned_keys or key in seen_keys:
            continue

        haystack = f"{title} {cand.get('subtitle', '')}"
        if not _series_words_present(series_name, haystack):
            continue
        if not _author_matches(author, cand.get("authors", [])):
            continue

        seen_keys.add(key)
        missing.append({
            "title": title,
            "author": ", ".join(cand.get("authors", [])) or author or "Unknown Author",
            "series": series_name,
            "series_index": _guess_series_index(title),
            "cover_url": cand.get("cover_url", ""),
        })

    missing.sort(key=lambda m: (m["series_index"] is None, m["series_index"] or 0, m["title"].lower()))
    return missing


def build_series_discovery_trigger(
    page: ft.Page,
    library: Library,
    on_results: Callable[[dict], None],
) -> ft.ElevatedButton:
    """Returns a "Weave My Strand" button. On click, checks every series
    in the library for volumes that don't appear to be on the shelf yet
    (skipping anything the user has already dismissed as a false
    positive) and calls on_results(missing_by_series) once done, where
    missing_by_series maps series name -> list of dicts from
    find_missing_series_volumes (only series with at least one hit are
    included)."""

    def _run(series_names: List[str]):
        owned_by_series: dict = {}
        author_by_series: dict = {}
        for book in library.all_books():
            if book.series:
                owned_by_series.setdefault(book.series, []).append(book.title)
                author_by_series.setdefault(book.series, book.author)

        missing_by_series = {}
        for name in series_names:
            found = find_missing_series_volumes(
                name, owned_by_series.get(name, []), author_by_series.get(name, "")
            )
            found = [v for v in found if not library.is_missing_dismissed(name, v["title"])]
            if found:
                missing_by_series[name] = found

        on_results(missing_by_series)

    def _on_click(e: ft.ControlEvent):
        series_names = library.series_names()
        if not series_names:
            page.open(ft.SnackBar(ft.Text("Add a book to a series first, then weave your strand.")))
            return
        page.open(ft.SnackBar(ft.Text("Weaving your strand — searching for volumes you might be missing…")))
        page.update()
        thread = threading.Thread(target=_run, args=(series_names,), daemon=True)
        thread.start()

    return ft.ElevatedButton(
        text="Weave My Strand",
        icon=ft.Icons.AUTO_AWESOME,
        on_click=_on_click,
    )


def render_missing_volume_ghost(
    volume: dict,
    on_add: Callable[[dict], None],
    on_dismiss: Optional[Callable[[dict], None]] = None,
) -> ft.Container:
    """A dim, 0.6-opacity ghost card for a volume that looks like it
    belongs to a series the user owns, but isn't in the library yet.
    Tapping "Add to Shelf" hands the volume dict back via on_add.
    Since no free series-metadata source is perfect, "Not in this
    series" lets the user permanently dismiss a false positive instead
    of it resurfacing every time Weave My Strand runs again."""
    idx_label = f" #{volume['series_index']:g}" if volume.get("series_index") else ""

    if volume.get("cover_url"):
        cover = ft.Container(
            width=40, height=60, border_radius=4, opacity=0.6,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Image(src=volume["cover_url"], width=40, height=60, fit=ft.BoxFit.COVER),
        )
    else:
        cover = ft.Container(
            width=40, height=60, border_radius=4,
            bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.WHITE),
        )

    action_buttons = [ft.TextButton("Add to Shelf", on_click=lambda e: on_add(volume))]
    if on_dismiss:
        action_buttons.append(
            ft.TextButton("Not in this series", on_click=lambda e: on_dismiss(volume))
        )

    return ft.Container(
        opacity=0.6,
        padding=10,
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
        content=ft.Row(
            controls=[
                cover,
                ft.Column(
                    expand=True,
                    spacing=2,
                    controls=[
                        ft.Text(f"{volume['title']}{idx_label}", italic=True, size=13),
                        ft.Text("Not in your library yet", size=11, italic=True),
                    ],
                ),
                ft.Column(controls=action_buttons, spacing=0),
            ],
        ),
    )


# ---------------- ghost placeholder card (metadata lazy-load) ----------------

def _ghost_card(title: str) -> ft.Container:
    """A dim, 0.6-opacity placeholder standing in for a book whose
    metadata is still being fetched."""
    return ft.Container(
        opacity=0.6,
        padding=10,
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
        content=ft.Row(
            controls=[
                ft.Container(
                    width=40, height=60,
                    bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.WHITE),
                    border_radius=4,
                ),
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text(title, italic=True, size=13),
                        ft.Text("Searching Google Books…", size=11, italic=True),
                        ft.ProgressRing(width=14, height=14, stroke_width=2),
                    ],
                ),
            ],
        ),
    )


# ---------------- the lazy-load trigger (existing feature, unchanged) ----------------

def build_lazy_load_trigger(
    page: ft.Page,
    library: Library,
    target_column: ft.Column,
    render_book_card: Callable[[Book], ft.Control],
    on_book_updated: Optional[Callable[[Book], None]] = None,
) -> ft.ElevatedButton:
    """
    Returns an ElevatedButton. On click it:
      1. Finds books missing metadata/covers in the library.
      2. Immediately renders a 0.6-opacity ghost placeholder for each
         one inside `target_column`.
      3. Kicks off a background thread that calls the Google Books API
         for each missing book one at a time.
      4. As each result comes back, swaps that book's ghost placeholder
         for the real card produced by `render_book_card`.
    """

    def _run_background_search(missing_books: List[Book], placeholder_index: dict):
        for book in missing_books:
            meta = search_google_books(book.title, book.author)
            if meta:
                apply_metadata(book, meta)
                library.save()

            def _swap(book=book):
                ghost = placeholder_index.get(book.id)
                if ghost in target_column.controls:
                    idx = target_column.controls.index(ghost)
                    target_column.controls[idx] = render_book_card(book)
                if on_book_updated:
                    on_book_updated(book)
                page.update()

            _swap()

    def _on_click(e: ft.ControlEvent):
        missing_books = library.books_missing_metadata()
        if not missing_books:
            page.open(ft.SnackBar(ft.Text("Every book already has full metadata.")))
            return

        placeholder_index = {}
        for book in missing_books:
            ghost = _ghost_card(book.title)
            placeholder_index[book.id] = ghost
            target_column.controls.append(ghost)
        page.update()

        thread = threading.Thread(
            target=_run_background_search,
            args=(missing_books, placeholder_index),
            daemon=True,
        )
        thread.start()

    return ft.ElevatedButton(
        text="Find Missing Book Info",
        icon=ft.Icons.AUTO_STORIES,
        on_click=_on_click,
    )

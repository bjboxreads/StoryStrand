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

from models import Book, Library

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

def _guess_series_index(title: str) -> Optional[float]:
    match = SERIES_INDEX_PATTERN.search(title)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def find_missing_series_volumes(series_name: str, owned_titles: List[str], author: str = "") -> List[dict]:
    """Best-effort discovery: search Google Books for a series name and
    return any volumes that don't match a title already in owned_titles.

    Google Books has no first-class "series" field, so this searches by
    series name (+ author, when known) and keeps only results that
    actually mention the series name somewhere in the title/subtitle -
    Google's search is loose enough to otherwise return unrelated hits.
    Results already on the shelf (matched case-insensitively) are
    dropped, and whatever's left is sorted by a best-guess series index.
    """
    query_parts = [f'"{series_name}"']
    if author and author != "Unknown Author":
        query_parts.append(f'inauthor:{author}')
    params = {"q": " ".join(query_parts), "maxResults": 40}

    try:
        resp = requests.get(GOOGLE_BOOKS_ENDPOINT, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    owned_lower = {t.strip().lower() for t in owned_titles}
    series_lower = series_name.strip().lower()
    seen_titles = set()
    missing = []

    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        title = (info.get("title") or "").strip()
        if not title:
            continue
        title_key = title.lower()
        if title_key in owned_lower or title_key in seen_titles:
            continue

        subtitle = (info.get("subtitle") or "").lower()
        if series_lower not in title_key and series_lower not in subtitle:
            continue  # doesn't actually look like part of this series

        seen_titles.add(title_key)
        image_links = info.get("imageLinks", {})
        missing.append({
            "title": title,
            "author": ", ".join(info.get("authors", [])) or author or "Unknown Author",
            "series": series_name,
            "series_index": _guess_series_index(title),
            "cover_url": image_links.get("thumbnail") or image_links.get("smallThumbnail") or "",
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
    and calls on_results(missing_by_series) once done, where
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


def render_missing_volume_ghost(volume: dict, on_add: Callable[[dict], None]) -> ft.Container:
    """A dim, 0.6-opacity ghost card for a volume that looks like it
    belongs to a series the user owns, but isn't in the library yet.
    Tapping "Add to Shelf" hands the volume dict back via on_add."""
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
                ft.TextButton("Add to Shelf", on_click=lambda e: on_add(volume)),
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

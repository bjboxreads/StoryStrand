"""
StoryStrand - google_books.py

Metadata lookups against the Google Books API, plus the specific
lazy-loading pattern requested:

  A trigger button, when clicked, calls a background function that
  searches the Google Books API for missing volumes, and renders ghost
  placeholders (0.6 opacity) inside a Column immediately, then swaps
  each placeholder for the real book card as its data arrives.

This file is self-contained: it exposes `build_lazy_load_trigger(...)`
which returns a ready-to-place ft.ElevatedButton wired to a target
ft.Column. Drop the button + column into your existing layout - nothing
here touches or rewrites the rest of main.py.
"""

from __future__ import annotations
import threading
import requests
import flet as ft
from typing import Callable, List, Optional

from models import Book, Library

GOOGLE_BOOKS_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"


# ---------------- raw API call ----------------

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
    if not book.isbn and meta.get("isbn"):
        book.isbn = meta["isbn"]
    book.metadata_fetched = True


# ---------------- ghost placeholder card ----------------

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


# ---------------- the lazy-load trigger ----------------

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

    Nothing about your existing layout needs to change - just place the
    returned button near `target_column` wherever you already build
    your UI.
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

            # UI updates must happen back on Flet's event loop; Flet
            # controls can be mutated from a worker thread as long as
            # page.update() is called afterward, which is what we do here.
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

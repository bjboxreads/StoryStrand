"""
StoryStrand - models.py
Core data model + JSON-backed library store.

Book identity: each book belongs to an author, and optionally to a series.
Books with no series are treated as their own standalone "branch" under
that author in the tree view.
"""

from __future__ import annotations
import json
import uuid
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict


DATA_FILE = os.path.join(os.path.dirname(__file__), "library.json")


@dataclass
class Book:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    author: str = "Unknown Author"
    series: Optional[str] = None          # None/"" => standalone book
    series_index: Optional[float] = None  # position within series, if known
    isbn: str = ""
    cover_url: str = ""
    page_count: Optional[int] = None
    description: str = ""
    genre: str = ""
    published: str = ""
    publisher: str = ""
    read: bool = False
    favorite: bool = False
    manual_entry: bool = False
    metadata_fetched: bool = False        # has Google Books already been tried?

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Book":
        known = {k: v for k, v in d.items() if k in Book.__dataclass_fields__}
        return Book(**known)


class Library:
    """In-memory book collection with JSON persistence."""

    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self.books: Dict[str, Book] = {}
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.books = {b["id"]: Book.from_dict(b) for b in raw}
            except (json.JSONDecodeError, OSError):
                self.books = {}
        else:
            self.books = {}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([b.to_dict() for b in self.books.values()], f, indent=2)

    # ---------- CRUD ----------

    def add_book(self, book: Book, persist: bool = True) -> Book:
        self.books[book.id] = book
        if persist:
            self.save()
        return book

    def update_book(self, book_id: str, **changes) -> Optional[Book]:
        book = self.books.get(book_id)
        if not book:
            return None
        for k, v in changes.items():
            if hasattr(book, k):
                setattr(book, k, v)
        self.save()
        return book

    def remove_book(self, book_id: str) -> None:
        self.books.pop(book_id, None)
        self.save()

    # ---------- queries used by the UI ----------

    def all_books(self) -> List[Book]:
        return list(self.books.values())

    def authors(self) -> List[str]:
        return sorted({b.author for b in self.books.values()}, key=str.lower)

    def series_names(self) -> List[str]:
        return sorted({b.series for b in self.books.values() if b.series}, key=str.lower)

    def favorites(self) -> List[Book]:
        return [b for b in self.books.values() if b.favorite]

    def read_books(self) -> List[Book]:
        return [b for b in self.books.values() if b.read]

    def unread_books(self) -> List[Book]:
        return [b for b in self.books.values() if not b.read]

    def search(self, query: str) -> List[Book]:
        q = query.strip().lower()
        if not q:
            return self.all_books()
        out = []
        for b in self.books.values():
            haystack = " ".join(
                [b.title, b.author, b.series or "", b.genre, b.isbn, b.publisher]
            ).lower()
            if q in haystack:
                out.append(b)
        return out

    def books_missing_metadata(self) -> List[Book]:
        """Books that have never had a Google Books lookup, or are missing
        a cover/page count - candidates for the lazy-load pass."""
        return [
            b for b in self.books.values()
            if not b.metadata_fetched or not b.cover_url or not b.page_count
        ]

    def tree(self) -> Dict[str, Dict[str, List[Book]]]:
        """
        Build the author -> branch -> [books] structure used by the tree view.
        Branch key is the series name for series books, or the book's own
        title (prefixed) for standalone books, so each standalone book gets
        its own branch as requested.
        """
        result: Dict[str, Dict[str, List[Book]]] = {}
        for b in sorted(self.books.values(), key=lambda x: (x.author.lower(), x.title.lower())):
            author_bucket = result.setdefault(b.author, {})
            if b.series:
                branch_key = f"series::{b.series}"
            else:
                branch_key = f"standalone::{b.id}"
            author_bucket.setdefault(branch_key, []).append(b)

        # sort books within a series branch by series_index when available
        for author_bucket in result.values():
            for branch_key, books in author_bucket.items():
                if branch_key.startswith("series::"):
                    books.sort(key=lambda x: (x.series_index is None, x.series_index or 0, x.title.lower()))
        return result

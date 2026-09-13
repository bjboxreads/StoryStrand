"""
StoryStrand - models.py
Core data model + JSON-backed library store.

Book identity: each book belongs to an author, and optionally to a series.
Books with no series are treated as their own standalone "branch" under
that author in the tree view.
"""

from __future__ import annotations
import json
import re
import uuid
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Set, Tuple


APP_DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA", os.path.dirname(__file__))
DATA_FILE = os.path.join(APP_DATA_DIR, "library.json")
DISMISSED_FILE = os.path.join(APP_DATA_DIR, "dismissed_missing.json")


def normalize_title_key(title: str) -> str:
    """Normalize a title for fuzzy-duplicate matching: strips
    parenthetical/bracketed annotations (edition info, series tags,
    "(Unabridged)", etc.) and punctuation, so e.g. "Mistborn: The Final
    Empire (2016 Ed.)" and "Mistborn: The Final Empire" key the same.
    Used both to dedupe Google Books' multiple editions of one title
    and to match a discovered volume against what's already owned."""
    text = re.sub(r"[\(\[].*?[\)\]]", "", title or "")
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


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

    def __init__(self, path: str = DATA_FILE, dismissed_path: str = DISMISSED_FILE):
        self.path = path
        self.dismissed_path = dismissed_path
        self.books: Dict[str, Book] = {}
        self.dismissed: Set[Tuple[str, str]] = set()  # (series_lower, normalized_title)
        self.load()
        self._load_dismissed()

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

    def _load_dismissed(self) -> None:
        if os.path.exists(self.dismissed_path):
            try:
                with open(self.dismissed_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.dismissed = {(item["series"], item["title_key"]) for item in raw}
            except (json.JSONDecodeError, OSError, KeyError):
                self.dismissed = set()
        else:
            self.dismissed = set()

    def _save_dismissed(self) -> None:
        with open(self.dismissed_path, "w", encoding="utf-8") as f:
            json.dump(
                [{"series": s, "title_key": k} for s, k in sorted(self.dismissed)],
                f, indent=2,
            )

    def is_missing_dismissed(self, series: str, title: str) -> bool:
        return (series.strip().lower(), normalize_title_key(title)) in self.dismissed

    def dismiss_missing_volume(self, series: str, title: str) -> None:
        """Weave My

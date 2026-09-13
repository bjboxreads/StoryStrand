"""
StoryStrand - importers.py
Turns uploaded CSV / TXT / DOCX / PDF / DOC files into Book objects.

CSV is the most reliable path (expects columns like title/author/series);
the other formats are parsed heuristically, one book guess per line, in
the form "Title - Author" or "Title (Series #N) - Author". Anything that
can't be parsed cleanly is still imported with a best-effort title so the
user can fix it up manually afterward.

--- FIXES APPLIED (see comments marked "FIX:") ---
1. FIX: added detect_series_from_title() / detect_series_index() /
   clean_title_for_lookup(), ported from the old app's data.py
   (detect_series / detect_series_number / clean_title_for_lookup).
   The old app used these as a fallback whenever a CSV had no usable
   series column (or the cell was blank) - the new importer was missing
   this entirely, so every book without an explicit "series" column
   silently came in as a standalone with the raw "(Series, #N)" still
   stuck in the title, which is why a real import showed "0 series"
   despite hundreds of books actually belonging to series.
2. FIX: added clean_isbn(), also ported from the old app, which strips
   the `="..."` wrapper Excel adds when it exports an ISBN column as
   text (otherwise Excel would treat a 13-digit ISBN as a number and
   mangle it). Without this, an ISBN cell exported by Excel/Goodreads
   came through as the literal string `="9781234567890"` instead of
   the ISBN.
"""

from __future__ import annotations
import csv
import os
import re
from typing import List, Optional, Tuple
from models import Book

SUPPORTED_EXTENSIONS = {".csv", ".txt", ".docx", ".pdf", ".doc"}

# "Title (Series Name #2) - Author"  or  "Title - Author"
LINE_PATTERN = re.compile(
    r"^\s*(?P<title>.+?)"
    r"(?:\s*\((?P<series>.+?)(?:\s*#\s*(?P<index>[\d.]+))?\))?"
    r"\s*(?:-|–|—|,)\s*(?P<author>.+?)\s*$"
)

# FIX: title-embedded series patterns, ported from the old app's
# detect_series()/detect_series_number() so CSVs/lines with no
# explicit series column still get split into title + series + index.
_SERIES_PATTERNS = [
    r"\(([^()]*)#\s*(\d+(?:\.\d+)?)\)",
    r"\[([^\[\]]*)#\s*(\d+(?:\.\d+)?)\]",
    r"\(([^()]*)\bBook\s+(\d+(?:\.\d+)?)\)",
    r"\[([^\[\]]*)\bBook\s+(\d+(?:\.\d+)?)\]",
]
_SERIES_INDEX_PATTERNS = [
    r"#\s*(\d+(?:\.\d+)?)",
    r"\bBook\s+(\d+(?:\.\d+)?)",
    r"\bVol(?:ume)?\.?\s+(\d+(?:\.\d+)?)",
]
_SERIES_STRIP_PATTERNS = [
    r"\([^()]*#\s*\d+(?:\.\d+)?[^()]*\)",
    r"\[[^\[\]]*#\s*\d+(?:\.\d+)?[^\[\]]*\]",
    r"\([^()]*\bBook\s+\d+(?:\.\d+)?[^()]*\)",
    r"\[[^\[\]]*\bBook\s+\d+(?:\.\d+)?[^\[\]]*\]",
]


def detect_series_from_title(title: str) -> Optional[str]:
    """FIX: pulls a series name out of a title like 'Bride (Bride, #1)'
    or 'Mistborn [The Final Empire #1]'. Returns None if nothing matches
    (i.e. the book is a genuine standalone)."""
    text = str(title or "")
    for pattern in _SERIES_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            series = match.group(1)
            series = re.sub(r",?\s*#\s*\d+(?:\.\d+)?", "", series, flags=re.IGNORECASE)
            series = re.sub(r"\bBook\s+\d+(?:\.\d+)?", "", series, flags=re.IGNORECASE)
            series = re.sub(r"\s+", " ", series).strip(" ,-:")
            if series:
                return series
    return None


def detect_series_index(title: str) -> Optional[float]:
    """FIX: pulls the volume number out of a title, e.g. 'Bride, #1' -> 1.0."""
    for pattern in _SERIES_INDEX_PATTERNS:
        match = re.search(pattern, str(title or ""), re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
    return None


def clean_title_for_lookup(title: str) -> str:
    """FIX: strips the series/volume annotation back out of the title
    once it's been captured into series/series_index, so the book's
    stored title is just 'Bride' rather than 'Bride (Bride, #1)'."""
    text = str(title or "")
    for pattern in _SERIES_STRIP_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,-:")


def clean_isbn(raw) -> str:
    """FIX: strips Excel's `="..."` text-cell wrapper (added so a long
    ISBN doesn't get reinterpreted/rounded as a number) plus any other
    stray punctuation, matching the old app's clean_isbn()."""
    s = str(raw if raw is not None else "").strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    s = re.sub(r'^="?', "", s)
    s = re.sub(r'"?$', "", s)
    s = re.sub(r"[^0-9Xx]", "", s)
    return s.upper()


def _apply_title_series_detection(title: str, series: Optional[str], series_index: Optional[float]) -> Tuple[str, Optional[str], Optional[float]]:
    """FIX: shared by both the CSV path and the line-parsing path -
    if the caller didn't already have a series (e.g. from an explicit
    CSV column), fall back to detecting one from the title text, and
    strip the annotation out of the stored title either way."""
    if not series:
        series = detect_series_from_title(title)
    if series and series_index is None:
        series_index = detect_series_index(title)
    clean_title = clean_title_for_lookup(title) if series else title.strip()
    return (clean_title or title.strip()), series, series_index


def import_file(path: str) -> List[Book]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return _import_csv(path)
    if ext == ".txt":
        return _import_lines(_read_txt_lines(path))
    if ext == ".docx":
        return _import_lines(_read_docx_lines(path))
    if ext == ".pdf":
        return _import_lines(_read_pdf_lines(path))
    if ext == ".doc":
        return _import_lines(_read_doc_lines(path))
    raise ValueError(f"Unsupported file type: {ext}")


# ---------------- CSV ----------------

def _detect_read_status(row, col) -> bool:
    """FIX: the importer previously never set Book.read at all, so
    every imported book silently defaulted to unread regardless of
    what the CSV said - this is why "Read" showed 0 and "Unread"
    showed the full count even for a Goodreads-style export that
    clearly has read/unread info in it.

    Checks, in order: an explicit "read" column (true/false/yes/1),
    then a shelf/status column (Goodreads' "Exclusive Shelf", or
    plain "Status"/"Shelf"), then falls back to "has a Date Read
    value" as a last resort signal."""
    read_col_raw = col(row, "read").strip().lower()
    if read_col_raw:
        return read_col_raw in ("true", "1", "1.0", "yes", "y", "read")

    shelf = col(row, "exclusive shelf", "shelf", "status", "read status").strip().lower()
    if shelf:
        if "currently" in shelf:
            return False
        if shelf in ("read", "finished") or ("read" in shelf and "to-read" not in shelf and "to read" not in shelf):
            return True
        if "to-read" in shelf or "to read" in shelf or "want" in shelf or "wishlist" in shelf:
            return False
        return False

    date_read = col(row, "date read", "read date").strip()
    return bool(date_read)


def _import_csv(path: str) -> List[Book]:
    books = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample) if sample.strip() else True
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return books

    header = [h.strip().lower() for h in rows[0]] if has_header else None
    data_rows = rows[1:] if has_header else rows

    def col(row, *names, default=""):
        if not header:
            return default
        for n in names:
            if n in header:
                idx = header.index(n)
                if idx < len(row):
                    return row[idx].strip()
        return default

    for row in data_rows:
        if not row or not any(c.strip() for c in row):
            continue
        if header:
            title = col(row, "title", "book", "name")
            author = col(row, "author", "authors", default="Unknown Author")
            series = col(row, "series") or None
            series_index_raw = col(row, "series_index", "series #", "#")
            isbn = clean_isbn(col(row, "isbn"))  # FIX: was raw col(row, "isbn")
            genre = col(row, "genre")
            read = _detect_read_status(row, col)  # FIX: was never set at all before
        else:
            # no header: assume title, author, series order
            title = row[0].strip() if len(row) > 0 else ""
            author = row[1].strip() if len(row) > 1 else "Unknown Author"
            series = row[2].strip() if len(row) > 2 and row[2].strip() else None
            series_index_raw = ""
            isbn = ""
            genre = ""
            read = False

        if not title:
            continue

        series_index = None
        if series_index_raw:
            try:
                series_index = float(series_index_raw)
            except ValueError:
                series_index = None

        # FIX: fall back to title-embedded series detection whenever the
        # row didn't already give us a series (no column, or blank cell).
        title, series, series_index = _apply_title_series_detection(title, series, series_index)

        books.append(Book(
            title=title,
            author=author or "Unknown Author",
            series=series,

            series_index=series_index,
            isbn=isbn,
            genre=genre,
            read=read,  # FIX: was never passed at all
        ))
    return books


# ---------------- plain line parsing (TXT / DOCX / PDF / DOC) ----------------

def _import_lines(lines: List[str]) -> List[Book]:
    books = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = LINE_PATTERN.match(line)
        if match:
            title = match.group("title").strip()
            author = (match.group("author") or "Unknown Author").strip()
            series = match.group("series")
            series_index = None
            if match.group("index"):
                try:
                    series_index = float(match.group("index"))
                except ValueError:
                    pass
            series = series.strip() if series else None
            # FIX: LINE_PATTERN already captures an explicit "(Series #N)"
            # group when present, but titles that embed the series without
            # matching that exact shape still benefit from the same
            # fallback detection used on the CSV path.
            title, series, series_index = _apply_title_series_detection(title, series, series_index)
            books.append(Book(
                title=title,
                author=author or "Unknown Author",
                series=series,
                series_index=series_index,
            ))
        else:
            # fallback: whole line becomes the title, author unknown -
            # the user (or a later metadata lookup) can fill in the rest.
            # FIX: still worth checking for an embedded series here too.
            title, series, series_index = _apply_title_series_detection(line, None, None)
            books.append(Book(title=title, author="Unknown Author", series=series, series_index=series_index))
    return books


def _read_txt_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.readlines()


def _read_docx_lines(path: str) -> List[str]:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise RuntimeError(
            "python-docx is required to import .docx files. "
            "Install with: pip install python-docx"
        ) from e
    document = docx.Document(path)
    return [p.text for p in document.paragraphs]


def _read_pdf_lines(path: str) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(
            "pypdf is required to import .pdf files. "
            "Install with: pip install pypdf"
        ) from e
    reader = PdfReader(path)
    lines: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(text.splitlines())
    return lines


_DOC_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _read_doc_lines(path: str) -> List[str]:
    """Legacy .doc (Word 97-2003) is a binary OLE-compound-file format,
    not text -- there's no reliable pure-Python parser for it the way
    python-docx handles the newer, zip-based .docx. Previously this
    just re-read the raw bytes as if they were a text file, which
    silently produced garbage instead of book titles.

    This instead pulls out printable text heuristically: Word stores
    most user-entered text as UTF-16LE, so decoding as UTF-16LE and
    keeping only runs that look like actual words (letters, reasonable
    length) recovers plain, unformatted book lists reasonably well.
    Tables, headers/footers, and other structured content will not
    come through cleanly -- if this returns nothing usable, the caller
    is told to convert the file to .docx instead, which python-docx
    parses properly.
    """
    with open(path, "rb") as f:
        raw = f.read()

    lines: List[str] = []

    utf16_text = raw.decode("utf-16-le", errors="ignore")
    for chunk in re.split(r"[\r\n\x00]+", utf16_text):
        chunk = chunk.strip()
        if len(chunk) >= 4 and _DOC_WORD_RE.search(chunk):
            lines.append(chunk)

    if not lines:
        # Fallback: some .doc files keep runs of plain ASCII/Latin-1
        # text alongside (or instead of) UTF-16LE runs.
        latin1_text = raw.decode("latin-1", errors="ignore")
        for chunk in re.findall(r"[ -~]{4,}", latin1_text):
            if _DOC_WORD_RE.search(chunk):
                lines.append(chunk.strip())

    return lines

"""
StoryStrand - importers.py
Turns uploaded CSV / TXT / DOCX / PDF / DOC files into Book objects.

CSV is the most reliable path (expects columns like title/author/series);
the other formats are parsed heuristically, one book guess per line, in
the form "Title - Author" or "Title (Series #N) - Author". Anything that
can't be parsed cleanly is still imported with a best-effort title so the
user can fix it up manually afterward.
"""

from __future__ import annotations
import csv
import os
import re
from typing import List
from models import Book

SUPPORTED_EXTENSIONS = {".csv", ".txt", ".docx", ".pdf", ".doc"}

# "Title (Series Name #2) - Author"  or  "Title - Author"
LINE_PATTERN = re.compile(
    r"^\s*(?P<title>.+?)"
    r"(?:\s*\((?P<series>.+?)(?:\s*#\s*(?P<index>[\d.]+))?\))?"
    r"\s*(?:-|–|—|,)\s*(?P<author>.+?)\s*$"
)


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
            isbn = col(row, "isbn")
            genre = col(row, "genre")
        else:
            # no header: assume title, author, series order
            title = row[0].strip() if len(row) > 0 else ""
            author = row[1].strip() if len(row) > 1 else "Unknown Author"
            series = row[2].strip() if len(row) > 2 and row[2].strip() else None
            series_index_raw = ""
            isbn = ""
            genre = ""

        if not title:
            continue

        series_index = None
        if series_index_raw:
            try:
                series_index = float(series_index_raw)
            except ValueError:
                series_index = None

        books.append(Book(
            title=title,
            author=author or "Unknown Author",
            series=series,
            series_index=series_index,
            isbn=isbn,
            genre=genre,
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
            books.append(Book(
                title=title,
                author=author or "Unknown Author",
                series=series.strip() if series else None,
                series_index=series_index,
            ))
        else:
            # fallback: whole line becomes the title, author unknown -
            # the user (or a later metadata lookup) can fill in the rest
            books.append(Book(title=line, author="Unknown Author"))
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

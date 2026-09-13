# StoryStrand

A whimsical, vintage-romantic book organizer built with Flet (Python).

## Structure

- `models.py` — `Book` dataclass + `Library` (JSON-backed store, all CRUD
  and query logic: authors, series, tree, search, favorites, read/unread).
- `themes.py` — the 10 color palettes (Emerald Grimoire, Gilded Midnight,
  Velvet Rose, Autumn Ember, Moonlit Violet, Verdant Garden, Old World
  Atlas, Arcane Spell, Scarlet Manor, Obsidian Vale).
- `importers.py` — CSV / TXT / DOCX / PDF parsing into `Book` objects.
  CSV with a header row (`title,author,series,series_index,isbn,genre`)
  is the most reliable; other formats are parsed line-by-line with a
  `"Title (Series #2) - Author"` heuristic and fall back to title-only
  entries you can fix up manually.
- `google_books.py` — Google Books API lookup, plus
  `build_lazy_load_trigger(...)`: the button + background thread +
  0.6-opacity ghost placeholder pattern you described. It's fully
  self-contained — drop the returned button and a target `ft.Column`
  anywhere in a layout without touching that layout's structure.
- `main.py` — wires it all together: theme picker, search bar,
  Authors / Series / Read / Unread / Favorites tabs, the collapsible
  author → branch → books tree (a branch is a series if the book has
  one, otherwise the book gets its own standalone branch), manual
  add/edit dialog, and file import.

## Running it locally

```bash
python -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

This opens StoryStrand as a desktop window. Flet uses the same codebase
for mobile — see below for the Play Store path.

## CSV import format

Header row + any of these columns (case-insensitive):

```
title,author,series,series_index,isbn,genre
The Hobbit,J.R.R. Tolkien,,,9780547928227,Fantasy
A Game of Thrones,George R.R. Martin,A Song of Ice and Fire,1,9780553103540,Fantasy
```

Rows with no series become their own standalone branch under that author.

## Getting to the Google Play Store

Flet apps package to Android via `flet build apk` (or `aab` for a Play
Store upload):

```bash
pip install flet[all]
flet build aab
```

That produces an Android App Bundle you can upload through the Google
Play Console. A few things worth doing before that step:

1. Move the JSON file store (`library.json`) to Flet's app-storage path
   (`flet.app_storage_path`) so data survives on-device correctly —
   right now it writes next to the script, which is fine on desktop but
   not where Android expects writable storage.
2. Swap the plain `requests` calls in `google_books.py` for `httpx`
   async calls (or keep the thread-based approach — it already works,
   it just isn't async) if you want the Google Books lookups to feel
   snappier on mobile networks.
3. Add an app icon and splash screen via `flet build`'s asset options.

## Extending this safely

Each concern lives in its own file on purpose. If you want a change to,
say, only the Google Books lookup or only the theme palette, that file
can be edited on its own — nothing else needs to move.

Not yet built (good next steps): genre-based tabs/filters, drag-to-reorder
within a series, bulk edit, cover image caching to disk, a proper
about/settings tab, and packaging config (`pyproject.toml` flet section)
for the Play Store build.

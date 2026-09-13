"""
StoryStrand - main.py
A whimsical, vintage-romantic book organizer built with Flet.

Structure:
  - Left/top: theme picker + search bar + import/add/discover buttons
  - Tabs: Authors | Series | Books | Read | Unread | Favorites
  - Authors tab renders a collapsible tree: Author -> branch -> books.
    A branch is either a series (grouping every book in that series)
    or a single standalone book (its own branch), as requested.
  - Books tab is a flat, alphabetical view of every book in the library.
  - "Find Missing Book Info" button lazy-loads Google Books metadata
    for any book still missing a cover/page count, rendering 0.6-opacity
    ghost placeholders while it works (see google_books.py).
  - "Weave My Strand" button checks each series against Google Books
    and surfaces volumes that don't appear to be on the shelf yet as
    ghost cards inside the Series tab, with a one-click "Add to Shelf".

This file wires everything together but keeps each concern (data,
importing, theming, Google Books) in its own module so you can hand
any one piece to me later and say "only touch this file."

THEMING: colors/fonts are ported from the old app's palette system.
See themes.py for the palette dict and pill_radius()/status_stripe_color()
below for the two signature visual touches (asymmetric card corners,
colored left-edge stripes) that made the old design read as distinct.

--- FIXES APPLIED (see comments marked "FIX:") ---
1. FIX: wrapped the root control in ft.SafeArea so the header no longer
   renders underneath the phone's status bar / notch.
2. FIX: build_header_text() and build_stats_row() now call page.update()
   themselves instead of relying on a later, unrelated page.update()
   call to flush their changes to the client.
3. FIX: importing a file now offers to fetch metadata (cover, genre,
   description, publisher info) for the newly-added books right away,
   using a concurrent (4-worker) fetch with a visible progress dialog -
   matching the old app's Import-tab checkboxes + "Looked up X of Y..."
   status, instead of new imports sitting metadata-less until the user
   taps "Find Missing Book Info" themselves.
4. FIX: build_tree() (Authors tab) restyled to look like a dropping
   family tree - rounded box "nodes" for each author/series, connected
   by a vertical trunk line down to their children, each child prefixed
   with a small "└─" elbow. Still collapsible by default so this stays
   usable at 672+ authors instead of trying to lay everything out at
   once like a fixed genealogy chart would.
5. FIX (performance): build_tree() previously called render_book_card()
   for every book in every branch, for every author, immediately on
   every call - once at startup and again on every tab switch, search
   keystroke, and theme change. Setting the resulting column's
   visible=False hid it but did NOT avoid building it, so the app was
   still constructing 672+ full widget trees (image + 4 icon buttons +
   text) synchronously on the UI thread every time, which is what made
   the app feel "slow and glitchy" and made search/other tabs appear to
   hang - they were just queued behind that rebuild, not broken. Book
   cards for a branch are now only built the first time that branch is
   expanded (and cached on the column after that), so build_tree()
   itself only ever constructs cheap header/node widgets up front.
6. FIX (performance): build_flat_list() (Books/Read/Unread/Favorites)
   and build_series_view() (Series tab) previously rendered a full
   render_book_card() for every matching book, all at once, on every
   tab switch and every search keystroke - the same eager-build
   problem #5 fixed for Authors, just not fixed for these other tabs
   yet. Both now render in batches of PAGE_SIZE with a "Load more"
   button, so opening Books with 672 titles (or typing in search)
   only ever builds a first page of cards instead of all of them.
7. FIX (discoverability): "Find Missing Book Info" and "Weave My
   Strand" were appended to the END of the scrollable tab content,
   after every book card on that tab - on a tab with hundreds of
   books, that put them below a very long (and, pre-fix #6, very
   laggy) scroll, which read as "the button is gone." They're now
   part of the fixed header, next to Import/Add, so they're visible
   on every tab without scrolling.
"""

import threading

import flet as ft

from models import Library, Book
from themes import THEMES, DEFAULT_THEME, theme_names
from importers import import_file, SUPPORTED_EXTENSIONS
from google_books import (
    build_lazy_load_trigger,
    build_series_discovery_trigger,
    fetch_missing_metadata_parallel,
    render_missing_volume_ghost,
)


def pill_radius():
    """The old app's signature asymmetric container corners -
    deliberately uneven rather than a uniform rounded rect."""
    return ft.border_radius.only(top_left=5, top_right=20, bottom_left=5, bottom_right=20)


def small_pill_radius():
    return ft.border_radius.only(top_left=12, top_right=5, bottom_left=12, bottom_right=5)


PAGE_SIZE = 60  # FIX: how many book cards to build per "page" in flat/series views


def status_stripe_key(book: Book) -> str:
    """Returns a THEME key ('accent'/'accent2'/'muted') - caller
    resolves it against the active palette. This app's Book model
    only tracks read/unread (no "Currently Reading" state), so it
    maps to two of the old app's three stripe colors."""
    return "accent2" if book.read else "muted"


def main(page: ft.Page):
    page.title = "StoryStrand"
    page.padding = 0
    page.fonts = {
        "Cormorant": "https://fonts.gstatic.com/s/cormorantgaramond/v16/co3bmX5slCNuHLi8bLeY9MK7whWMhyjYrEtGhtRXO0k.ttf",
        "Baskerville": "https://fonts.gstatic.com/s/librebaskerville/v14/kmKnZrc3Hgbbcjq75U4uslyuy4kn0qNZaxLBpg.ttf",
    }

    library = Library()
    state = {
        "theme": DEFAULT_THEME,
        "search_query": "",
        "missing_by_series": {},  # series name -> [volume dicts] from Weave My Strand
        "visible_count": PAGE_SIZE,  # FIX: how many cards build_flat_list/build_series_view render before "Load more"
        "expanded_authors": set(),   # FIX: authors currently expanded in the Authors tab tree, survives refresh_body()
        "expanded_branches": set(),  # FIX: (author, branch_key) pairs currently expanded, survives refresh_body()
    }

    # ---------------- theming ----------------

    def apply_theme(theme_name: str):
        t = THEMES[theme_name]
        state["theme"] = theme_name
        page.bgcolor = t["page"]
        page.theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=t["accent"],
                secondary=t["accent2"],
                surface=t["surface"],
                on_surface=t["text"],
            ),
            font_family="Baskerville",
        )
        page.update()

    # ---------------- shared card renderer (also used by ghost loader) ----------------

    def render_book_card(book: Book) -> ft.Control:
        t = THEMES[state["theme"]]

        def toggle_read(e):
            library.update_book(book.id, read=not book.read)
            refresh_all()

        def toggle_fav(e):
            library.update_book(book.id, favorite=not book.favorite)
            refresh_all()

        def edit(e):
            open_book_dialog(book)

        def remove(e):
            def confirm_remove(ev):
                library.remove_book(book.id)
                page.close(confirm_dialog)
                refresh_all()

            confirm_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Remove book?"),
                content=ft.Text(f'Remove "{book.title}" from your shelf?'),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda ev: page.close(confirm_dialog)),
                    ft.TextButton("Remove", on_click=confirm_remove),
                ],
            )
            page.open(confirm_dialog)

        cover_radius = ft.border_radius.only(top_left=3, top_right=8, bottom_left=3, bottom_right=8)
        if book.cover_url:
            cover = ft.Container(
                width=44, height=64, border_radius=cover_radius,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                # FIX: was ft.BoxFit.COVER, which doesn't exist on this
                # Flet version and crashed rendering for every book that
                # had a cover_url - i.e. every tab except Authors (no
                # branches expanded yet) and Favorites (empty list).
                content=ft.Image(src=book.cover_url, width=44, height=64, fit=ft.ImageFit.COVER),
            )
        else:
            cover = ft.Container(
                width=44, height=64, border_radius=cover_radius,
                bgcolor=t["surface2"],
                alignment=ft.alignment.center,
                content=ft.Icon(ft.Icons.MENU_BOOK, size=20, color=t["accent"]),
            )

        # Subtitle carries series/#, page count, ISBN, and publisher -
        # only whichever of those the book actually has.
        subtitle_bits = []
        if book.series:
            idx = f" #{book.series_index:g}" if book.series_index else ""
            subtitle_bits.append(f"{book.series}{idx}")
        if book.page_count:
            subtitle_bits.append(f"{book.page_count} pgs")
        if book.publisher:
            subtitle_bits.append(book.publisher)
        if book.isbn:
            subtitle_bits.append(f"ISBN {book.isbn}")
        subtitle = "  •  ".join(subtitle_bits)

        return ft.Container(
            padding=10,
            border_radius=pill_radius(),
            bgcolor=t["surface"],
            border=ft.border.only(left=ft.BorderSide(4, t[status_stripe_key(book)])),
            content=ft.Row(
                controls=[
                    cover,
                    ft.Column(
                        expand=True,
                        spacing=0,
                        controls=[
                            ft.Text(book.title, size=14, weight=ft.FontWeight.BOLD,
                                     color=t["text"], font_family="Baskerville"),
                            ft.Text(subtitle, size=11, italic=True, color=t["muted"]),
                        ],
                    ),
                    ft.IconButton(
                        icon=ft.Icons.STAR if book.favorite else ft.Icons.STAR_BORDER,
                        icon_color=t["accent"],
                        tooltip="Favorite",
                        on_click=toggle_fav,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHECK_CIRCLE if book.read else ft.Icons.CHECK_CIRCLE_OUTLINE,
                        icon_color=t["accent2"],
                        tooltip="Read / Unread",
                        on_click=toggle_read,
                    ),
                    ft.IconButton(icon=ft.Icons.EDIT, icon_color=t["muted"], tooltip="Edit", on_click=edit),
                    ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=t["muted"], tooltip="Remove", on_click=remove),
                ],
            ),
        )

    # ---------------- collapsible tree (Authors tab) ----------------

    def build_tree(filtered_ids=None) -> ft.Control:
        """FIX: styled as a dropping family-tree — each author/series is a
        rounded 'node' box, and its children hang directly beneath it,
        indented and marked by a left trunk line, the way a family-tree
        chart branches from a parent box down to its children. Still
        fully collapsible (author/series start closed) so this stays
        usable with 672+ authors instead of trying to lay everything out
        at once like a fixed-size chart would.

        FIX: this deliberately avoids wrapping any child in a Row +
        expand=True Container. An earlier version did that for the
        trunk-line + elbow-connector look, and it caused book titles to
        render squeezed into a couple of pixels of width (wrapping one
        or two characters per line): a Row's flexible/expand child needs
        a parent with a definite bounded width to flex within, and
        nested this many levels deep inside plain Columns, that bound
        wasn't reliably there. Plain Column-in-Column nesting (as used
        below, and as the original working version of this app used)
        doesn't have that problem, so the indent + left border here
        stands in for the elbow/trunk-line visual instead.

        FIX (performance): book cards for a branch are now only built
        the first time that branch is expanded, not upfront for every
        branch of every author on every call to build_tree(). This is
        what fixes the "slow and glitchy" tab/search lag - previously
        render_book_card() ran for all 672+ books on every single
        build_tree() call, whether or not any branch was even expanded
        to show them.

        FIX (state persistence): which authors/branches are expanded is
        now tracked in state["expanded_authors"] / state["expanded_branches"]
        instead of living only on the Column objects built here. Every
        call to build_tree() constructs brand-new Column instances, so
        the old approach (col.visible flipped by the toggle) reset to
        fully collapsed on every single refresh - meaning any tap on a
        book's favorite/read/edit/delete icon (which calls refresh_all())
        silently collapsed the entire tree back shut, since a whole new
        set of visible=False columns replaced the ones you had open.
        That's what made book taps look like "nothing happens." Reading
        expanded state from `state` on every build instead means a
        refresh re-expands exactly what was open before.
        """
        t = THEMES[state["theme"]]
        tree_data = library.tree()
        author_controls = []

        for author, branches in tree_data.items():
            branch_controls = []
            for branch_key, books in branches.items():
                if filtered_ids is not None:
                    books = [b for b in books if b.id in filtered_ids]
                    if not books:
                        continue

                is_series = branch_key.startswith("series::")
                branch_label = branch_key.split("::", 1)[1] if is_series else books[0].title
                branch_icon = ft.Icons.COLLECTIONS_BOOKMARK if is_series else ft.Icons.BOOKMARK_BORDER

                if is_series:
                    # Chronological reading order within the series -
                    # books with no series_index sort last rather than
                    # colliding at the front with #1.
                    books = sorted(books, key=lambda b: (b.series_index is None, b.series_index or 0))

                # FIX: state key survives across build_tree() calls even
                # though the Column objects themselves don't.
                branch_state_key = (author, branch_key)
                branch_expanded = branch_state_key in state["expanded_branches"]

                # FIX (performance + state persistence): only build cards
                # for branches that are actually expanded right now - not
                # all of them (perf), and re-derived from `state` on every
                # call rather than relying on a cache that gets thrown
                # away the moment build_tree() runs again (persistence).
                book_column = ft.Column(
                    controls=[render_book_card(b) for b in books] if branch_expanded else [],
                    spacing=6,
                    visible=branch_expanded,
                )

                def make_toggle(col=book_column, branch_books=books, skey=branch_state_key):
                    def _toggle(e):
                        try:
                            if skey in state["expanded_branches"]:
                                state["expanded_branches"].discard(skey)
                                col.visible = False
                            else:
                                state["expanded_branches"].add(skey)
                                col.controls = [render_book_card(b) for b in branch_books]
                                col.visible = True
                            e.control.icon = ft.Icons.EXPAND_LESS if col.visible else ft.Icons.EXPAND_MORE
                            page.update()
                        except Exception as ex:
                            import traceback
                            traceback.print_exc()
                            page.open(ft.SnackBar(ft.Text(f"branch toggle failed: {ex}"), bgcolor="red"))
                            page.update()
                    return _toggle

                # FIX: branch header is now a rounded box ("node"),
                # matching the boxed-ancestor look of a family-tree
                # chart, rather than a plain unboxed row of text.
                branch_node = ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                    border_radius=small_pill_radius(),
                    bgcolor=t["surface2"],
                    content=ft.Row(
                        controls=[
                            ft.Icon(branch_icon, size=16, color=t["accent"]),
                            ft.Text(
                                branch_label if is_series else f"{branch_label} (standalone)",
                                size=13, color=t["text"],
                            ),
                            ft.Container(expand=True),
                            ft.Text(f"({len(books)})", size=11, color=t["muted"]),
                            ft.IconButton(
                                icon=ft.Icons.EXPAND_LESS if branch_expanded else ft.Icons.EXPAND_MORE,
                                icon_size=18, on_click=make_toggle(),
                            ),
                        ],
                    ),
                )
                # FIX: children hang directly under the node box, indented
                # and marked with a left trunk line - plain Column nesting,
                # no Row/expand involved.
                branch_controls.append(
                    ft.Column(
                        controls=[
                            branch_node,
                            ft.Container(
                                padding=ft.padding.only(left=16, top=6),
                                border=ft.border.only(left=ft.BorderSide(2, t["line"])),
                                content=book_column,
                            ),
                        ],
                        spacing=4,
                    )
                )

            if not branch_controls:
                continue

            # FIX: same persistence approach for the author-level toggle.
            author_expanded = author in state["expanded_authors"]
            author_column = ft.Column(controls=branch_controls, spacing=10, visible=author_expanded)

            def make_author_toggle(col=author_column, akey=author):
                def _toggle(e):
                    try:
                        if akey in state["expanded_authors"]:
                            state["expanded_authors"].discard(akey)
                            col.visible = False
                        else:
                            state["expanded_authors"].add(akey)
                            col.visible = True
                        e.control.icon = ft.Icons.EXPAND_LESS if col.visible else ft.Icons.EXPAND_MORE
                        page.update()
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        page.open(ft.SnackBar(ft.Text(f"author toggle failed: {ex}"), bgcolor="red"))
                        page.update()
                return _toggle

            total_books = sum(len(v) for v in branches.values())
            author_header = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.PERSON_OUTLINE, color=t["accent"]),
                    ft.Text(author, size=16, weight=ft.FontWeight.BOLD, color=t["text"],
                            font_family="Cormorant"),
                    ft.Container(expand=True),
                    ft.Text(f"({total_books})", size=12, color=t["muted"]),
                    ft.IconButton(
                        icon=ft.Icons.EXPAND_LESS if author_expanded else ft.Icons.EXPAND_MORE,
                        on_click=make_author_toggle(),
                    ),
                ],
            )
            # FIX: author node also has its branch boxes hanging directly
            # beneath it, indented + left-lined, same plain-Column nesting.
            author_controls.append(
                ft.Container(
                    padding=10, border_radius=pill_radius(), bgcolor=t["card"],
                    content=ft.Column(
                        controls=[
                            author_header,
                            ft.Container(
                                padding=ft.padding.only(left=12, top=4),
                                border=ft.border.only(left=ft.BorderSide(2, t["line"])),
                                content=author_column,
                            ),
                        ],
                        spacing=6,
                    ),
                )
            )

        if not author_controls:
            return ft.Container(
                padding=30,
                content=ft.Text("No books yet — import a file or add one manually.",
                                 italic=True, color=t["muted"]),
            )

        return ft.Column(controls=author_controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    def build_flat_list(books) -> ft.Control:
        """FIX (performance): only builds the first state['visible_count']
        cards instead of a render_book_card() for every book in the list
        up front - on Books/Read/Unread/Favorites that used to mean
        building all 672(ish) cards on every tab switch or search
        keystroke. A "Load more" button bumps visible_count and asks for
        another refresh_body() rather than eagerly building the rest."""
        if not books:
            t = THEMES[state["theme"]]
            return ft.Container(padding=30, content=ft.Text("Nothing here yet.", italic=True, color=t["muted"]))

        t = THEMES[state["theme"]]
        visible_count = min(state["visible_count"], len(books))
        controls = [render_book_card(b) for b in books[:visible_count]]

        remaining = len(books) - visible_count
        if remaining > 0:
            def _load_more(e):
                state["visible_count"] += PAGE_SIZE
                refresh_body()

            controls.append(
                ft.OutlinedButton(
                    f"Load more ({remaining} remaining)",
                    icon=ft.Icons.EXPAND_MORE,
                    on_click=_load_more,
                    style=ft.ButtonStyle(color=t["accent"]),
                )
            )

        return ft.Column(controls=controls, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    def build_books_view() -> ft.Control:
        """Books tab: every book, flat, alphabetical by title."""
        books = sorted(library.all_books(), key=lambda b: b.title.lower())
        return build_flat_list(books)

    def _drop_from_missing(volume: dict):
        series_name = volume.get("series")
        if series_name in state["missing_by_series"]:
            state["missing_by_series"][series_name] = [
                v for v in state["missing_by_series"][series_name]
                if v["title"] != volume["title"]
            ]
            if not state["missing_by_series"][series_name]:
                del state["missing_by_series"][series_name]

    def add_missing_volume(volume: dict):
        """Called when the user taps "Add to Shelf" on a Weave My
        Strand ghost card - adds it as a real book and drops it from
        the pending-discovery list for that series."""
        library.add_book(Book(
            title=volume["title"],
            author=volume.get("author") or "Unknown Author",
            series=volume.get("series"),
            series_index=volume.get("series_index"),
            cover_url=volume.get("cover_url", ""),
        ))
        _drop_from_missing(volume)
        refresh_all()

    def dismiss_missing_volume(volume: dict):
        """Called when the user taps "Not in this series" on a ghost
        card - permanently remembers that (series, title) shouldn't be
        surfaced again, and drops it from view right away."""
        library.dismiss_missing_volume(volume.get("series", ""), volume["title"])
        _drop_from_missing(volume)
        refresh_all()

    def build_series_view() -> ft.Control:
        """FIX (performance): renders series blocks (and the book cards
        inside them) only until state['visible_count'] total books have
        been rendered, then stops and offers "Load more series" instead
        of building every book in every series up front - previously
        this built a render_book_card() for every owned book in every
        series, all at once, on every tab switch and search keystroke."""
        t = THEMES[state["theme"]]
        by_series = {}
        for b in library.all_books():
            if b.series:
                by_series.setdefault(b.series, []).append(b)

        # A series can show up here purely from a Weave My Strand
        # discovery even before the user owns any book in it yet -
        # otherwise a freshly-discovered series with zero owned books
        # would have nowhere to render its ghost cards.
        for series_name in state["missing_by_series"]:
            by_series.setdefault(series_name, [])

        if not by_series:
            return ft.Container(padding=30, content=ft.Text("No series tracked yet.", italic=True, color=t["muted"]))

        blocks = []
        rendered_books = 0
        remaining_series_count = 0
        for series_name, books in sorted(by_series.items(), key=lambda kv: kv[0].lower()):
            if rendered_books >= state["visible_count"]:
                remaining_series_count += 1
                continue

            books.sort(key=lambda x: (x.series_index is None, x.series_index or 0))
            owned_cards = [render_book_card(b) for b in books]
            rendered_books += len(books)
            ghost_cards = [
                render_missing_volume_ghost(v, on_add=add_missing_volume, on_dismiss=dismiss_missing_volume)
                for v in state["missing_by_series"].get(series_name, [])
            ]
            blocks.append(
                ft.Container(
                    padding=10, border_radius=pill_radius(), bgcolor=t["surface"],
                    content=ft.Column(controls=[
                        ft.Text(series_name, size=15, weight=ft.FontWeight.BOLD,
                                color=t["text"], font_family="Cormorant"),
                        ft.Column(controls=owned_cards + ghost_cards, spacing=6),
                    ]),
                )
            )

        if remaining_series_count > 0:
            def _load_more(e):
                state["visible_count"] += PAGE_SIZE
                refresh_body()

            blocks.append(
                ft.OutlinedButton(
                    f"Load more series ({remaining_series_count} remaining)",
                    icon=ft.Icons.EXPAND_MORE,
                    on_click=_load_more,
                    style=ft.ButtonStyle(color=t["accent"]),
                )
            )

        return ft.Column(controls=blocks, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------------- add / edit dialog ----------------

    def open_book_dialog(book: Book = None, prefill: dict = None):
        editing = book is not None
        prefill = prefill or {}

        def initial(field_name, default=""):
            if editing:
                return getattr(book, field_name) or default
            return prefill.get(field_name, default)

        title_f = ft.TextField(label="Title", value=str(initial("title")))
        author_f = ft.TextField(label="Author", value=str(initial("author")))
        series_f = ft.TextField(label="Series (optional)", value=str(initial("series")))
        series_idx_val = initial("series_index")
        series_idx_f = ft.TextField(
            label="# in series (optional)",
            value=str(series_idx_val) if series_idx_val else "",
        )
        genre_f = ft.TextField(label="Genre (optional)", value=str(initial("genre")))
        cover_f = ft.TextField(label="Cover image URL (optional)", value=str(initial("cover_url")))
        isbn_f = ft.TextField(label="ISBN (optional)", value=str(initial("isbn")))
        publisher_f = ft.TextField(label="Publisher (optional)", value=str(initial("publisher")))
        page_count_val = initial("page_count")
        page_count_f = ft.TextField(
            label="Page count (optional)",
            value=str(page_count_val) if page_count_val else "",
        )
        published_f = ft.TextField(label="Published date (optional)", value=str(initial("published")))
        description_f = ft.TextField(
            label="Description (optional)", value=str(initial("description")),
            multiline=True, min_lines=2, max_lines=5,
        )

        def save(e):
            idx_val = None
            if series_idx_f.value.strip():
                try:
                    idx_val = float(series_idx_f.value.strip())
                except ValueError:
                    idx_val = None

            page_count_num = None
            if page_count_f.value.strip():
                try:
                    page_count_num = int(float(page_count_f.value.strip()))
                except ValueError:
                    page_count_num = None

            shared_fields = dict(
                series=series_f.value.strip() or None,
                series_index=idx_val,
                genre=genre_f.value.strip(),
                cover_url=cover_f.value.strip(),
                isbn=isbn_f.value.strip(),
                publisher=publisher_f.value.strip(),
                page_count=page_count_num,
                published=published_f.value.strip(),
                description=description_f.value.strip(),
            )

            if editing:
                library.update_book(
                    book.id,
                    title=title_f.value.strip() or book.title,
                    author=author_f.value.strip() or "Unknown Author",
                    **shared_fields,
                )
            else:
                library.add_book(Book(
                    title=title_f.value.strip(),
                    author=author_f.value.strip() or "Unknown Author",
                    manual_entry=True,
                    **shared_fields,
                ))
            page.close(dialog)
            refresh_all()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Book" if editing else "Add Book Manually"),
            content=ft.Column(
                controls=[
                    title_f, author_f, series_f, series_idx_f, genre_f, cover_f,
                    isbn_f, publisher_f, page_count_f, published_f, description_f,
                ],
                tight=True, width=380, scroll=ft.ScrollMode.AUTO, height=460,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.close(dialog)),
                ft.FilledButton("Save", on_click=save),
            ],
        )
        page.open(dialog)

    # ---------------- import ----------------

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    def on_files_picked(e: ft.FilePickerResultEvent):
        if not e.files:
            return
        files = list(e.files)

        # Parsing (especially .doc/.pdf heuristic extraction) can take
        # a real moment on a big file - doing it inline here used to
        # freeze the UI thread for that whole time. Running it on a
        # background thread keeps the app responsive; refresh_all()
        # and the result snackbar only fire once parsing is done.
        page.open(ft.SnackBar(ft.Text(f"Importing {len(files)} file(s)…")))
        page.update()

        def _run_import():
            imported_count = 0
            errors = []
            newly_added: list[Book] = []
            for f in files:
                try:
                    new_books = import_file(f.path)
                    for b in new_books:
                        library.add_book(b, persist=False)
                        newly_added.append(b)
                    imported_count += len(new_books)
                except Exception as ex:
                    errors.append(f"{f.name}: {ex}")
            library.save()
            msg = f"Imported {imported_count} book(s)."
            if errors:
                msg += " Some files had issues: " + "; ".join(errors)
            refresh_all()
            page.open(ft.SnackBar(ft.Text(msg)))
            page.update()
            # FIX: offer to fetch metadata for the books that just came
            # in, instead of leaving them metadata-less until the user
            # remembers to tap "Find Missing Book Info" later.
            if newly_added:
                open_fetch_metadata_dialog(newly_added)

        threading.Thread(target=_run_import, daemon=True).start()

    file_picker.on_result = on_files_picked

    def open_import_picker(e):
        file_picker.pick_files(
            allow_multiple=True,
            allowed_extensions=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        )

    # ---------------- FIX: post-import metadata fetch ----------------

    def open_fetch_metadata_dialog(new_books: list[Book]):
        """FIX: shown right after an import finishes. Mirrors the old
        app's Import-tab checkboxes (all checked by default) so
        fetching metadata for new books stays one click, but the user
        can still skip it or turn off individual fields. FIX: restyled
        to match the app's theme and voice instead of a plain default
        dialog with generic checkbox labels."""
        t = THEMES[state["theme"]]
        count = len(new_books)
        cover_cb = ft.Checkbox(label="🖼️  Cover art", value=True)
        desc_cb = ft.Checkbox(label="📝  Descriptions", value=True)
        genre_cb = ft.Checkbox(label="🏷️  Genres", value=True)
        pub_cb = ft.Checkbox(label="📚  Publisher, pages & publish date", value=True)

        def skip(ev):
            page.close(dlg)

        def start_fetch(ev):
            page.close(dlg)
            run_metadata_fetch(
                new_books,
                want_cover=cover_cb.value,
                want_description=desc_cb.value,
                want_genre=genre_cb.value,
                want_pubinfo=pub_cb.value,
            )

        dlg = ft.AlertDialog(
            modal=True,
            bgcolor=t["surface"],
            title=ft.Text(
                f"Weave in the details for {count} new book(s)?",
                color=t["accent"], font_family="Cormorant", size=22,
            ),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Fill in whatever's missing using Google Books and OpenLibrary:",
                        color=t["muted"], size=12, italic=True,
                    ),
                    ft.Divider(color=t["line"], height=1),
                    cover_cb, desc_cb, genre_cb, pub_cb,
                ],
                tight=True,
                width=340,
                spacing=8,
            ),
            actions=[
                ft.TextButton(
                    "Not Now", on_click=skip,
                    style=ft.ButtonStyle(color=t["muted"]),
                ),
                ft.FilledButton(
                    "✨ Weave It In", on_click=start_fetch,
                    style=ft.ButtonStyle(bgcolor=t["accent"], color=t["page"]),
                ),
            ],
        )
        page.open(dlg)

    def run_metadata_fetch(new_books: list[Book], want_cover, want_description,
                            want_genre, want_pubinfo):
        """FIX: runs the concurrent (4-worker) fetch from google_books.py
        against the newly imported books, with a live progress dialog -
        instead of either blocking the UI or leaving the user with no
        sense of whether it's working. FIX: restyled to match the app's
        theme and voice ("Weaving new threads...") instead of a plain
        default "Fetching metadata" dialog with a bare progress bar."""
        t = THEMES[state["theme"]]
        progress_text = ft.Text("Casting a line to Google Books & OpenLibrary…",
                                 color=t["muted"], size=12, italic=True)
        progress_bar = ft.ProgressBar(color=t["accent"], bgcolor=t["surface2"], width=280)
        progress_dlg = ft.AlertDialog(
            modal=True,
            bgcolor=t["surface"],
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.AUTO_AWESOME, color=t["accent"], size=20),
                    ft.Text("Weaving new threads…", color=t["accent"],
                            font_family="Cormorant", size=22),
                ],
                spacing=8,
            ),
            content=ft.Column(
                controls=[progress_bar, progress_text],
                tight=True, width=300, spacing=10,
            ),
        )
        page.open(progress_dlg)
        page.update()

        def on_progress(done, total):
            progress_bar.value = done / total if total else None
            progress_text.value = f"Looked up {done} of {total} book(s)…"
            page.update()

        def _run():
            stats = fetch_missing_metadata_parallel(
                new_books,
                want_cover=want_cover,
                want_description=want_description,
                want_genre=want_genre,
                want_pubinfo=want_pubinfo,
                on_progress=on_progress,
            )
            library.save()
            page.close(progress_dlg)
            refresh_all()
            page.open(ft.SnackBar(
                ft.Text(f"✨ Wove in details for {stats['found']} of {stats['total']} book(s).")
            ))
            page.update()

        threading.Thread(target=_run, daemon=True).start()

    # ---------------- Weave My Strand (series discovery) ----------------

    def handle_missing_results(missing_by_series: dict):
        state["missing_by_series"] = missing_by_series
        total = sum(len(v) for v in missing_by_series.values())
        if total:
            page.open(ft.SnackBar(
                ft.Text(f"Found {total} possible missing volume(s) — check the Series tab.")
            ))
        else:
            page.open(ft.SnackBar(ft.Text("Didn't find any volumes you're missing. Your strand is complete!")))
        refresh_all()

    weave_button = build_series_discovery_trigger(
        page=page, library=library, on_results=handle_missing_results,
    )

    # ---------------- search + tabs ----------------

    search_field = ft.TextField(
        hint_text="Search title, author, series, genre, ISBN…",
        prefix_icon=ft.Icons.SEARCH,
        expand=True,
        on_change=lambda e: on_search_change(e.control.value),
    )

    body_holder = ft.Container(expand=True)
    # FIX: bounded height + its own internal scroll. This previously had
    # no size limit and got moved into the fixed header (see below) - with
    # ~370 ghost placeholders stacking up during a big "Find Missing Book
    # Info" run, an unbounded column there made the header itself balloon
    # to thousands of pixels tall (headers aren't inside the page's main
    # scroll region), which is exactly what made the app look frozen and
    # unscrollable with only the first couple of ghost cards visible.
    lazy_load_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=220)

    TAB_LABELS = ["Authors", "Series", "Books", "Read", "Unread", "Favorites"]
    tab_row = ft.Row(spacing=8, wrap=True, run_spacing=8)

    def current_tab_index():
        return state["selected_tab"]

    def build_tab_row():
        """A small self-contained tab selector, built from plain
        Containers rather than ft.Tabs/TabBar - Flet's Tabs API has
        changed shape across releases, so this avoids main.py breaking
        again on a future flet upgrade."""
        t = THEMES[state["theme"]]
        buttons = []
        for i, label in enumerate(TAB_LABELS):
            selected = state["selected_tab"] == i

            def make_click(idx=i):
                def _click(e):
                    try:
                        state["selected_tab"] = idx
                        state["visible_count"] = PAGE_SIZE  # FIX: reset pagination on tab change
                        build_tab_row()
                        refresh_body()
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        page.open(ft.SnackBar(ft.Text(f"tab click failed: {ex}"), bgcolor="red"))
                        page.update()
                return _click

            buttons.append(
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=14, vertical=8),
                    border_radius=small_pill_radius(),
                    bgcolor=t["accent"] if selected else t["surface"],
                    on_click=make_click(),
                    content=ft.Text(
                        label,
                        color=t["page"] if selected else t["text"],
                        weight=ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL,
                        size=13,
                        font_family="Baskerville",
                    ),
                )
            )
        tab_row.controls = buttons

    def build_stats_row():
        """The old app's row of 6 accent-bordered stat tiles
        (Books/Authors/Series/Read/Unread/Favorites) - a signature
        piece of its look that had no equivalent here at all.
        FIX: these tiles previously had no on_click at all - tapping
        "Read", "Series", etc. did nothing. They now jump straight to
        the matching tab, same as the old app's stat tiles did."""
        t = THEMES[state["theme"]]
        books = library.all_books()
        total = len(books)
        authors = len({b.author for b in books}) if total else 0
        series_count = len({b.series for b in books if b.series}) if total else 0
        read_count = sum(1 for b in books if b.read)
        unread_count = sum(1 for b in books if not b.read)
        favorites = sum(1 for b in books if b.favorite)
        pairs = [
            (total, "Books"), (authors, "Authors"), (series_count, "Series"),
            (read_count, "Read"), (unread_count, "Unread"), (favorites, "Favorites"),
        ]
        # FIX: maps each stat tile's label to the matching tab index in
        # TAB_LABELS = ["Authors", "Series", "Books", "Read", "Unread", "Favorites"].
        label_to_tab_index = {label: idx for idx, label in enumerate(TAB_LABELS)}

        def stat_tile(number, label):
            def _on_click(e):
                try:
                    state["selected_tab"] = label_to_tab_index[label]
                    state["visible_count"] = PAGE_SIZE  # FIX: reset pagination on tab change
                    build_tab_row()
                    refresh_body()
                except Exception as ex:
                    import traceback
                    traceback.print_exc()
                    page.open(ft.SnackBar(ft.Text(f"tile click failed: {ex}"), bgcolor="red"))
                    page.update()

            return ft.Container(
                content=ft.Column(
                    [
                        ft.Text(str(number), size=24, weight=ft.FontWeight.BOLD,
                                 color=t["accent"], font_family="Cormorant"),
                        ft.Text(label, size=11, color=t["text"], font_family="Baskerville",
                                 no_wrap=True, text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=2,
                ),
                bgcolor=t["card"],
                border=ft.border.all(1, t["accent"]),
                border_radius=small_pill_radius(),
                padding=ft.padding.symmetric(horizontal=6, vertical=12),
                alignment=ft.alignment.center,
                width=106,
                on_click=_on_click,  # FIX: was missing entirely
                ink=True,            # FIX: gives a visible tap ripple, like the old app's tiles
            )

        stats_row.controls = [stat_tile(n, l) for n, l in pairs]
        page.update()  # FIX: push this change immediately instead of relying on a later, unrelated update()

    def build_header_text():
        """Centered title + italic tagline, colored from the active
        theme - previously plain default-colored text with no tagline
        at all, which is most of why the header didn't read as the
        same app. FIX: also colors the new ornamental divider under
        the tagline so it tracks the active theme's accent color too."""
        t = THEMES[state["theme"]]
        header_title.value = "StoryStrand"
        header_title.color = t["accent"]
        header_tagline.color = t["muted"]
        header_divider_left.bgcolor = t["accent"]
        header_divider_right.bgcolor = t["accent"]
        header_divider_icon.color = t["accent"]
        page.update()  # FIX: push this change immediately instead of relying on a later, unrelated update()

    def on_search_change(value):
        try:
            state["search_query"] = value
            state["visible_count"] = PAGE_SIZE  # FIX: reset pagination whenever the query changes
            refresh_body()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            page.open(ft.SnackBar(ft.Text(f"search failed: {ex}"), bgcolor="red"))
            page.update()

    def refresh_body():
        try:
            query = state["search_query"]
            idx = current_tab_index()

            if query:
                matched_ids = {b.id for b in library.search(query)}
                if idx == 0:
                    content = build_tree(filtered_ids=matched_ids)
                elif idx == 1:
                    content = build_flat_list([b for b in library.search(query) if b.series])
                elif idx == 2:
                    content = build_flat_list(
                        sorted(library.search(query), key=lambda b: b.title.lower())
                    )
                elif idx == 3:
                    content = build_flat_list([b for b in library.search(query) if b.read])
                elif idx == 4:
                    content = build_flat_list([b for b in library.search(query) if not b.read])
                else:
                    content = build_flat_list([b for b in library.search(query) if b.favorite])
            else:
                if idx == 0:
                    content = build_tree()
                elif idx == 1:
                    content = build_series_view()
                elif idx == 2:
                    content = build_books_view()
                elif idx == 3:
                    content = build_flat_list(library.read_books())
                elif idx == 4:
                    content = build_flat_list(library.unread_books())
                else:
                    content = build_flat_list(library.favorites())

            # FIX (discoverability): lazy_load_trigger_row (Find Missing
            # Book Info / Weave My Strand) and lazy_load_column used to be
            # appended here, after every book card on the tab - buried
            # below hundreds of cards. They now live in the fixed header
            # instead, so body_holder only ever holds the active tab's
            # own content.
            body_holder.content = content
            page.update()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            page.open(ft.SnackBar(ft.Text(f"refresh_body failed: {ex}"), bgcolor="red"))
            page.update()

    def refresh_all():
        build_stats_row()
        refresh_body()

    state["selected_tab"] = 0
    build_tab_row()

    theme_dropdown = ft.Dropdown(
        label="Theme",
        value=DEFAULT_THEME,
        options=[ft.DropdownOption(key=name, text=name) for name in theme_names()],
        on_change=lambda e: (
            apply_theme(e.control.value), build_header_text(), build_tab_row(),
            build_stats_row(), refresh_body(),
        ),
        width=200,
    )

    lazy_load_trigger = build_lazy_load_trigger(
        page=page,
        library=library,
        target_column=lazy_load_column,
        render_book_card=render_book_card,
        on_book_updated=lambda b: None,
    )
    lazy_load_trigger_row = ft.Row(controls=[lazy_load_trigger, weave_button], wrap=True)

    header_title = ft.Text(
        "StoryStrand", size=40, weight=ft.FontWeight.BOLD,
        font_family="Cormorant", text_align=ft.TextAlign.CENTER,
        style=ft.TextStyle(letter_spacing=2),  # FIX: a touch of letter-spacing reads less "default app title"
    )
    # FIX: corrected tagline wording per request.
    header_tagline = ft.Text(
        "Where every story finds its thread", italic=True,
        size=13, font_family="Baskerville", text_align=ft.TextAlign.CENTER,
    )
    # FIX: "prettier" -> "romantic": swapped the plain book-icon divider
    # for a small heart-flourish glyph (a classic vintage-romance motif),
    # and gave the whole title block a soft drop shadow for a bit of
    # dimension instead of sitting flat on the background.
    header_divider_left = ft.Container(width=44, height=1)
    header_divider_right = ft.Container(width=44, height=1)
    header_divider_icon = ft.Text("❦", size=16, font_family="Cormorant")
    header_divider = ft.Row(
        controls=[header_divider_left, header_divider_icon, header_divider_right],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )
    stats_row = ft.Row(spacing=8, wrap=True, run_spacing=8)

    header = ft.Container(
        padding=16,
        content=ft.Column(
            spacing=10,
            controls=[
                # FIX: soft shadow behind the title block for a touch of
                # romantic depth, instead of flat text on a flat background.
                ft.Container(
                    shadow=ft.BoxShadow(
                        blur_radius=24, spread_radius=1,
                        color=ft.Colors.with_opacity(0.25, ft.Colors.BLACK),
                        offset=ft.Offset(0, 4),
                    ),
                    content=ft.Column(
                        controls=[header_title, header_tagline, header_divider],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                ),
                ft.Row(controls=[ft.Container(expand=True), theme_dropdown]),
                stats_row,
                ft.Row(
                    controls=[
                        search_field,
                        ft.ElevatedButton("Import File", icon=ft.Icons.UPLOAD_FILE, on_click=open_import_picker),
                        ft.ElevatedButton("Add Book", icon=ft.Icons.ADD, on_click=lambda e: open_book_dialog()),
                    ],
                ),
                tab_row,
                # FIX (discoverability): these used to be appended at the
                # bottom of the scrollable tab content, below every book
                # card. Moved into the fixed header so they're visible on
                # every tab without scrolling past hundreds of cards.
                lazy_load_trigger_row,
                lazy_load_column,
            ],
        ),
    )

    # FIX: wrap the root control in SafeArea so the header respects the
    # device status bar / notch instead of rendering underneath it.
    page.add(
        ft.SafeArea(
            content=ft.Column(
                controls=[header, body_holder],
                expand=True,
            ),
            expand=True,
        )
    )

    apply_theme(DEFAULT_THEME)
    build_header_text()
    build_stats_row()
    refresh_body()


if __name__ == "__main__":
    ft.app(target=main)

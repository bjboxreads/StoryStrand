"""
StoryStrand - main.py
A whimsical, vintage-romantic book organizer built with Flet.

Structure:
  - Header: title, theme picker, stats tiles, search bar, import/add buttons
  - Tabs: Authors | Series | Books | Read | Unread | Favorites
  - Authors tab renders a collapsible tree: Author -> branch -> books.
    A branch is either a series (grouping every book in that series)
    or a single standalone book (its own branch).
  - Books tab is a flat, alphabetical view of every book in the library.
  - "Find Missing Book Info" lazy-loads Google Books metadata with
    0.6-opacity ghost placeholders (see google_books.py).
  - "Weave My Strand" checks each series against Google Books and
    surfaces volumes that aren't on the shelf yet as ghost cards.

PERFORMANCE NOTES (latest round):
  - soft_shadow() now returns None. Blurred drop shadows on hundreds of
    cards/boxes were a major source of lag on phones.
  - build_tree() (Authors tab) now builds an author's branch boxes only
    when that author is expanded, and shows authors a page at a time
    with a "Load more authors" button, instead of building every
    author's whole subtree on every refresh.
  - Book cards in flat/series views already load in pages (PAGE_SIZE).

LATEST CHANGES:
  - The chosen color theme is now saved on the device and restored on
    startup (it used to reset to the default every time the app opened).
    The custom palette is also applied in the phone's dark mode, not
    just light mode.
  - Book cards are now tap-to-expand: tapping a card drops down its
    details (author, genre, published date, description). Which cards
    are open is remembered in state["expanded_books"].
  - While searching, the Authors tab opens every matching author and
    series automatically so results are visible without extra clicks.
  - Book cards keep the title row roomy on phones: only star and read
    stay on the row; Edit and Remove live in the expanded panel.
  - Manually added books now auto-fill cover, description, genre and
    publisher in the background (only fields left blank).
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


def soft_shadow(blur=10, opacity=0.18, dy=3):
    """PERFORMANCE: shadows are turned off. Blurred shadows on every
    card, tree box and stat tile made tab/theme switches laggy on
    phones. Returning None means 'no shadow' everywhere this is used.
    (The single big title shadow in the header is separate and kept.)"""
    return None


PAGE_SIZE = 60  # how many cards/authors to build per "page" before "Load more"

THEME_STORAGE_KEY = "storystrand_theme"  # where the chosen theme name is saved on the device


def status_stripe_key(book: Book) -> str:
    """Returns a THEME key ('accent2'/'muted') - caller resolves it
    against the active palette."""
    return "accent2" if book.read else "muted"


def main(page: ft.Page):
    page.title = "StoryStrand"
    page.padding = 0
    page.fonts = {
        "Cormorant": "https://fonts.gstatic.com/s/cormorantgaramond/v16/co3bmX5slCNuHLi8bLeY9MK7whWMhyjYrEtGhtRXO0k.ttf",
        "Baskerville": "https://fonts.gstatic.com/s/librebaskerville/v14/kmKnZrc3Hgbbcjq75U4uslyuy4kn0qNZaxLBpg.ttf",
        "Playfair Display": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
        "Dancing Script": "https://raw.githubusercontent.com/google/fonts/main/ofl/dancingscript/DancingScript%5Bwght%5D.ttf",
    }

    # ---------------- saved theme (survives app restarts) ----------------

    def load_saved_theme() -> str:
        """Returns the theme the user last picked, or DEFAULT_THEME if
        nothing is saved yet (or the saved name no longer exists)."""
        try:
            saved = page.client_storage.get(THEME_STORAGE_KEY)
            if saved in THEMES:
                return saved
        except Exception:
            import traceback
            traceback.print_exc()
        return DEFAULT_THEME

    def save_theme(theme_name: str):
        try:
            page.client_storage.set(THEME_STORAGE_KEY, theme_name)
        except Exception:
            import traceback
            traceback.print_exc()

    library = Library()
    state = {
        "theme": load_saved_theme(),
        "search_query": "",
        "missing_by_series": {},  # series name -> [volume dicts] from Weave My Strand
        "visible_count": PAGE_SIZE,  # how many cards/authors render before "Load more"
        "expanded_authors": set(),   # authors currently expanded in the Authors tab tree
        "expanded_branches": set(),  # (author, branch_key) pairs currently expanded
        "expanded_books": set(),     # book ids whose detail panel is open
    }

    # ---------------- theming ----------------

    def apply_theme(theme_name: str):
        t = THEMES[theme_name]
        state["theme"] = theme_name
        page.bgcolor = t["page"]
        theme_obj = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=t["accent"],
                secondary=t["accent2"],
                surface=t["surface"],
                on_surface=t["text"],
            ),
            font_family="Baskerville",
        )
        # page.theme only applies in the phone's LIGHT mode; page.dark_theme
        # applies in DARK mode. Set both so the palette looks the same
        # whichever mode the phone is in.
        page.theme = theme_obj
        page.dark_theme = theme_obj
        page.update()

    # ---------------- shared empty-state renderer ----------------

    def empty_state(message: str) -> ft.Control:
        t = THEMES[state["theme"]]
        return ft.Container(
            padding=36,
            alignment=ft.alignment.center,
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.AUTO_STORIES, size=30, color=t["accent"]),
                    ft.Text(
                        message, size=20, font_family="Dancing Script",
                        color=t["muted"], text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
        )

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
                bgcolor=t["surface"],
                title=ft.Text("Remove book?", color=t["accent"], font_family="Cormorant", size=22),
                content=ft.Text(f'Remove "{book.title}" from your shelf?', color=t["text"]),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda ev: page.close(confirm_dialog),
                                  style=ft.ButtonStyle(color=t["muted"])),
                    ft.TextButton("Remove", on_click=confirm_remove,
                                  style=ft.ButtonStyle(color=t["accent2"])),
                ],
            )
            page.open(confirm_dialog)

        cover_radius = ft.border_radius.only(top_left=3, top_right=8, bottom_left=3, bottom_right=8)
        if book.cover_url:
            cover = ft.Container(
                width=44, height=64, border_radius=cover_radius,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
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

        # ---- expandable details panel (tap the card to open/close) ----
        detail_widgets = []
        if book.author:
            detail_widgets.append(ft.Text(f"by {book.author}", size=12, color=t["text"]))
        if book.genre:
            detail_widgets.append(ft.Text(f"Genre: {book.genre}", size=12, color=t["muted"]))
        if book.published:
            detail_widgets.append(ft.Text(f"Published: {book.published}", size=12, color=t["muted"]))
        if book.description:
            detail_widgets.append(ft.Text(book.description, size=12, color=t["text"]))
        if not detail_widgets:
            detail_widgets.append(
                ft.Text("No extra details yet. Tap Edit to add some.",
                        size=12, italic=True, color=t["muted"])
            )

        # Edit / Remove live in the expanded panel so the title row has
        # room to breathe on a phone-width screen.
        action_row = ft.Row(
            controls=[
                ft.TextButton("Edit", icon=ft.Icons.EDIT, on_click=edit,
                              style=ft.ButtonStyle(color=t["muted"])),
                ft.TextButton("Remove", icon=ft.Icons.DELETE_OUTLINE, on_click=remove,
                              style=ft.ButtonStyle(color=t["muted"])),
            ],
            spacing=4,
            wrap=True,
        )

        is_open = book.id in state["expanded_books"]
        details = ft.Container(
            visible=is_open,
            padding=ft.padding.only(top=8, left=4, right=4),
            content=ft.Column(controls=detail_widgets + [action_row], spacing=4),
        )
        chevron = ft.Icon(
            ft.Icons.EXPAND_LESS if is_open else ft.Icons.EXPAND_MORE,
            size=18, color=t["muted"],
        )

        def toggle_details(e):
            if book.id in state["expanded_books"]:
                state["expanded_books"].discard(book.id)
                details.visible = False
                chevron.name = ft.Icons.EXPAND_MORE
            else:
                state["expanded_books"].add(book.id)
                details.visible = True
                chevron.name = ft.Icons.EXPAND_LESS
            page.update()

        return ft.Container(
            padding=10,
            border_radius=pill_radius(),
            bgcolor=t["surface"],
            border=ft.border.only(left=ft.BorderSide(4, t[status_stripe_key(book)])),
            shadow=soft_shadow(),
            on_click=toggle_details,
            ink=True,
            content=ft.Column(
                spacing=0,
                controls=[
                    ft.Row(
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
                            chevron,
                            ft.IconButton(
                                icon=ft.Icons.STAR if book.favorite else ft.Icons.STAR_BORDER,
                                icon_color=t["accent"],
                                tooltip="Favorite",
                                on_click=toggle_fav,
                                icon_size=22, width=36, height=36,
                                style=ft.ButtonStyle(padding=0),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CHECK_CIRCLE if book.read else ft.Icons.CHECK_CIRCLE_OUTLINE,
                                icon_color=t["accent2"],
                                tooltip="Read / Unread",
                                on_click=toggle_read,
                                icon_size=22, width=36, height=36,
                                style=ft.ButtonStyle(padding=0),
                            ),
                        ],
                    ),
                    details,
                ],
            ),
        )

    # ---------------- collapsible tree (Authors tab) ----------------

    def build_tree(filtered_ids=None) -> ft.Control:
        """Authors tab as a collapsible family-tree.

        PERFORMANCE: only builds what's visible.
          - Authors are shown PAGE_SIZE at a time with a "Load more
            authors" button.
          - An author's branch boxes are only built once that author is
            expanded.
          - A branch's book cards are only built once that branch is
            expanded.
        Which authors/branches are open is tracked in
        state["expanded_authors"] / state["expanded_branches"], so a
        refresh (e.g. tapping a book's favorite star) keeps them open.

        SEARCH: when filtered_ids is given (the user is searching),
        every matching author and branch is shown already opened so
        results are visible without extra clicks.

        NOTE: this deliberately avoids wrapping children in a Row +
        expand=True Container (that squeezed titles to a few pixels).
        Plain Column-in-Column nesting with an indent + left border is
        used instead.
        """
        t = THEMES[state["theme"]]
        tree_data = library.tree()
        searching = filtered_ids is not None  # when searching, show matches already opened up

        # Cheap pass (no widgets): which authors have anything to show?
        authors_to_show = []
        for author, branches in tree_data.items():
            kept = {}
            for branch_key, books in branches.items():
                if filtered_ids is not None:
                    books = [b for b in books if b.id in filtered_ids]
                if books:
                    kept[branch_key] = books
            if kept:
                authors_to_show.append((author, kept))

        if not authors_to_show:
            return empty_state("No books yet — import a file or add one manually.")

        def build_branch_controls(author, kept):
            branch_controls = []
            for branch_key, books in kept.items():
                is_series = branch_key.startswith("series::")
                branch_label = branch_key.split("::", 1)[1] if is_series else books[0].title
                branch_icon = ft.Icons.COLLECTIONS_BOOKMARK if is_series else ft.Icons.BOOKMARK_BORDER

                if is_series:
                    # Chronological reading order within the series -
                    # books with no series_index sort last.
                    books = sorted(books, key=lambda b: (b.series_index is None, b.series_index or 0))

                branch_state_key = (author, branch_key)
                branch_expanded = searching or branch_state_key in state["expanded_branches"]

                book_column = ft.Column(
                    controls=[render_book_card(b) for b in books] if branch_expanded else [],
                    spacing=6,
                    visible=branch_expanded,
                )

                def make_toggle(col=book_column, branch_books=books, skey=branch_state_key):
                    def _toggle(e):
                        try:
                            if col.visible:
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

                branch_node = ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                    border_radius=small_pill_radius(),
                    bgcolor=t["surface2"],
                    shadow=soft_shadow(blur=6, opacity=0.15, dy=2),
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
            return branch_controls

        visible_authors = authors_to_show[:state["visible_count"]]
        remaining = len(authors_to_show) - len(visible_authors)

        author_controls = []
        for author, kept in visible_authors:
            author_expanded = searching or author in state["expanded_authors"]
            author_column = ft.Column(
                controls=build_branch_controls(author, kept) if author_expanded else [],
                spacing=10,
                visible=author_expanded,
            )

            def make_author_toggle(col=author_column, akey=author, akept=kept):
                def _toggle(e):
                    try:
                        if col.visible:
                            state["expanded_authors"].discard(akey)
                            col.visible = False
                        else:
                            state["expanded_authors"].add(akey)
                            col.controls = build_branch_controls(akey, akept)
                            col.visible = True
                        e.control.icon = ft.Icons.EXPAND_LESS if col.visible else ft.Icons.EXPAND_MORE
                        page.update()
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        page.open(ft.SnackBar(ft.Text(f"author toggle failed: {ex}"), bgcolor="red"))
                        page.update()
                return _toggle

            total_books = sum(len(v) for v in kept.values())
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
            author_controls.append(
                ft.Container(
                    padding=10, border_radius=pill_radius(), bgcolor=t["card"],
                    shadow=soft_shadow(),
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

        if remaining > 0:
            def _load_more_authors(e):
                state["visible_count"] += PAGE_SIZE
                refresh_body()

            author_controls.append(
                ft.OutlinedButton(
                    f"Load more authors ({remaining} remaining)",
                    icon=ft.Icons.EXPAND_MORE,
                    on_click=_load_more_authors,
                    style=ft.ButtonStyle(color=t["accent"]),
                )
            )

        return ft.Column(controls=author_controls, spacing=10)

    def build_flat_list(books) -> ft.Control:
        """Only builds the first state['visible_count'] cards; a "Load
        more" button bumps visible_count and refreshes."""
        if not books:
            return empty_state("Nothing here yet.")

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

        return ft.Column(controls=controls, spacing=8)

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
        """Renders series blocks (and the book cards inside them) only
        until state['visible_count'] total books have been rendered,
        then offers "Load more series"."""
        t = THEMES[state["theme"]]
        by_series = {}
        for b in library.all_books():
            if b.series:
                by_series.setdefault(b.series, []).append(b)

        # A series can show up here purely from a Weave My Strand
        # discovery even before the user owns any book in it yet.
        for series_name in state["missing_by_series"]:
            by_series.setdefault(series_name, [])

        if not by_series:
            return empty_state("No series tracked yet.")

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
                    shadow=soft_shadow(),
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

        return ft.Column(controls=blocks, spacing=10)

    # ---------------- auto-fill for manually added books ----------------

    def auto_fill_new_book(book: Book):
        """After a manual add, quietly look up the cover, description,
        genre and publisher in the background (same Google Books ->
        OpenLibrary lookup the import uses) and fill in ONLY what the
        user left blank - anything they typed is never overwritten."""
        if not (book.title or "").strip():
            return

        page.open(ft.SnackBar(ft.Text(f'Looking up details for "{book.title}"…')))
        page.update()

        def _run():
            try:
                stats = fetch_missing_metadata_parallel([book])
                library.save()
                refresh_all()
                if stats.get("found"):
                    msg = f'✨ Filled in details for "{book.title}".'
                else:
                    msg = f'Couldn\'t find details online for "{book.title}". You can tap Edit to add them.'
            except Exception as ex:
                import traceback
                traceback.print_exc()
                msg = f"Detail lookup failed: {ex}"
            page.open(ft.SnackBar(ft.Text(msg)))
            page.update()

        threading.Thread(target=_run, daemon=True).start()

    # ---------------- add / edit dialog ----------------

    def open_book_dialog(book: Book = None, prefill: dict = None):
        editing = book is not None
        prefill = prefill or {}
        t = THEMES[state["theme"]]

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

            new_book = None
            if editing:
                library.update_book(
                    book.id,
                    title=title_f.value.strip() or book.title,
                    author=author_f.value.strip() or "Unknown Author",
                    **shared_fields,
                )
            else:
                new_book = Book(
                    title=title_f.value.strip(),
                    author=author_f.value.strip() or "Unknown Author",
                    manual_entry=True,
                    **shared_fields,
                )
                library.add_book(new_book)
            page.close(dialog)
            refresh_all()
            if new_book is not None:
                auto_fill_new_book(new_book)

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=t["surface"],
            title=ft.Text(
                "Edit Book" if editing else "Add Book Manually",
                color=t["accent"], font_family="Cormorant", size=24,
            ),
            content=ft.Column(
                controls=[
                    title_f, author_f, series_f, series_idx_f, genre_f, cover_f,
                    isbn_f, publisher_f, page_count_f, published_f, description_f,
                ],
                tight=True, width=380, scroll=ft.ScrollMode.AUTO, height=460,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.close(dialog),
                              style=ft.ButtonStyle(color=t["muted"])),
                ft.FilledButton("Save", on_click=save,
                                style=ft.ButtonStyle(bgcolor=t["accent"], color=t["page"])),
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

        # Parsing can take a real moment on a big file, so it runs on a
        # background thread to keep the app responsive.
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
            # Offer to fetch metadata for the books that just came in.
            if newly_added:
                open_fetch_metadata_dialog(newly_added)

        threading.Thread(target=_run_import, daemon=True).start()

    file_picker.on_result = on_files_picked

    def open_import_picker(e):
        file_picker.pick_files(
            allow_multiple=True,
            allowed_extensions=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        )

    # ---------------- post-import metadata fetch ----------------

    def open_fetch_metadata_dialog(new_books: list[Book]):
        """Shown right after an import finishes. Checkboxes are all on
        by default so fetching metadata for new books stays one click,
        but the user can skip it or turn off individual fields."""
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
        """Runs the concurrent (4-worker) fetch from google_books.py
        against the newly imported books, with a live progress dialog."""
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
    lazy_load_column = ft.Column(spacing=6)  # ghost placeholders render here

    TAB_LABELS = ["Authors", "Series", "Books", "Read", "Unread", "Favorites"]
    tab_row = ft.Row(spacing=8, wrap=True, run_spacing=8)

    def current_tab_index():
        return state["selected_tab"]

    def build_tab_row():
        """A small self-contained tab selector, built from plain
        Containers rather than ft.Tabs - Flet's Tabs API has changed
        shape across releases, so this avoids breaking on upgrades."""
        t = THEMES[state["theme"]]
        buttons = []
        for i, label in enumerate(TAB_LABELS):
            selected = state["selected_tab"] == i

            def make_click(idx=i):
                def _click(e):
                    try:
                        state["selected_tab"] = idx
                        state["visible_count"] = PAGE_SIZE  # reset pagination on tab change
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
        """Row of 6 accent-bordered stat tiles
        (Books/Authors/Series/Read/Unread/Favorites). Tapping a tile
        jumps to the matching tab."""
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
        # Maps each tile's label to the matching tab index in TAB_LABELS.
        label_to_tab_index = {label: idx for idx, label in enumerate(TAB_LABELS)}

        def stat_tile(number, label):
            def _on_click(e):
                try:
                    state["selected_tab"] = label_to_tab_index[label]
                    state["visible_count"] = PAGE_SIZE  # reset pagination on tab change
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
                shadow=soft_shadow(blur=6, opacity=0.15, dy=2),
                padding=ft.padding.symmetric(horizontal=6, vertical=12),
                alignment=ft.alignment.center,
                width=106,
                on_click=_on_click,
                ink=True,
            )

        stats_row.controls = [stat_tile(n, l) for n, l in pairs]
        page.update()

    def build_header_text():
        """Centered title + tagline, colored from the active theme,
        plus the ornamental divider under the tagline."""
        t = THEMES[state["theme"]]
        header_title.value = "StoryStrand"
        header_title.color = t["accent"]
        header_tagline.color = t["muted"]
        header_divider_left.bgcolor = t["accent"]
        header_divider_right.bgcolor = t["accent"]
        header_divider_icon.color = t["accent"]
        page.update()

    def on_search_change(value):
        try:
            state["search_query"] = value
            state["visible_count"] = PAGE_SIZE  # reset pagination whenever the query changes
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

            # There is exactly ONE scrollable region for the whole body -
            # this wrapping Column. The trigger buttons (Find Missing Book
            # Info / Weave My Strand) live in the fixed header; the ghost
            # card list (lazy_load_column) lives here in the scrolling body
            # so it can grow or shrink without resizing anything else.
            body_holder.content = ft.Column(
                key=f"body-{idx}-{query}",
                controls=[content, lazy_load_column],
                expand=True, scroll=ft.ScrollMode.AUTO,
            )
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

    def on_theme_change(e):
        """Theme dropdown: apply the pick, remember it for next launch,
        then repaint everything that uses palette colors."""
        apply_theme(e.control.value)
        save_theme(e.control.value)
        build_header_text()
        build_tab_row()
        build_stats_row()
        refresh_body()

    theme_dropdown = ft.Dropdown(
        label="Theme",
        value=state["theme"],  # the saved theme (or the default on first launch)
        options=[ft.DropdownOption(key=name, text=name) for name in theme_names()],
        on_change=on_theme_change,
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
        "StoryStrand", size=44, weight=ft.FontWeight.BOLD,
        font_family="Playfair Display", text_align=ft.TextAlign.CENTER,
        style=ft.TextStyle(letter_spacing=0.5),
    )
    header_tagline = ft.Text(
        "Where every story finds its thread",
        size=22, font_family="Dancing Script", text_align=ft.TextAlign.CENTER,
    )
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
                # Soft shadow behind the title block - the one shadow kept.
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
                lazy_load_trigger_row,
            ],
        ),
    )

    # SafeArea keeps the header clear of the device status bar / notch.
    page.add(
        ft.SafeArea(
            content=ft.Column(
                controls=[header, body_holder],
                expand=True,
            ),
            expand=True,
        )
    )

    apply_theme(state["theme"])
    build_header_text()
    build_stats_row()
    refresh_body()


if __name__ == "__main__":
    ft.app(target=main)

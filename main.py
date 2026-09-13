"""
StoryStrand - main.py
A whimsical, vintage-romantic book organizer built with Flet.

Structure:
  - Left/top: theme picker + search bar + import/add buttons
  - Tabs: Authors | Series | Read | Unread | Favorites
  - Authors tab renders a collapsible tree: Author -> branch -> books.
    A branch is either a series (grouping every book in that series)
    or a single standalone book (its own branch), as requested.
  - "Find Missing Book Info" button lazy-loads Google Books metadata
    for any book still missing a cover/page count, rendering 0.6-opacity
    ghost placeholders while it works (see google_books.py).

This file wires everything together but keeps each concern (data,
importing, theming, Google Books) in its own module so you can hand
any one piece to me later and say "only touch this file."
"""

import flet as ft

from models import Library, Book
from themes import THEMES, DEFAULT_THEME, theme_names
from importers import import_file, SUPPORTED_EXTENSIONS
from google_books import build_lazy_load_trigger


def main(page: ft.Page):
    page.title = "StoryStrand"
    page.padding = 0
    page.fonts = {
        "Vintage-Display": "https://cdn.jsdelivr.net/gh/google/fonts/ofl/cinzeldecorative/CinzelDecorative-Bold.ttf",
        "Vintage-Body": "https://cdn.jsdelivr.net/gh/google/fonts/ofl/eb-garamond/EBGaramond%5Bwght%5D.ttf",
    }

    library = Library()
    state = {"theme": DEFAULT_THEME, "search_query": ""}

    # ---------------- theming ----------------

    def apply_theme(theme_name: str):
        t = THEMES[theme_name]
        state["theme"] = theme_name
        page.bgcolor = t["background"]
        page.theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=t["primary"],
                secondary=t["secondary"],
                surface=t["surface"],
                on_surface=t["text"],
            ),
            font_family="Vintage-Body",
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

        if book.cover_url:
            cover = ft.Container(
                width=44, height=64, border_radius=4,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Image(src=book.cover_url, width=44, height=64, fit=ft.BoxFit.COVER),
            )
        else:
            cover = ft.Container(
                width=44, height=64, border_radius=4,
                bgcolor=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
                alignment=ft.alignment.center,
                content=ft.Icon(ft.Icons.MENU_BOOK, size=20, color=t["accent"]),
            )

        subtitle_bits = []
        if book.series:
            idx = f" #{book.series_index:g}" if book.series_index else ""
            subtitle_bits.append(f"{book.series}{idx}")
        if book.page_count:
            subtitle_bits.append(f"{book.page_count} pgs")
        subtitle = "  •  ".join(subtitle_bits)

        return ft.Container(
            padding=8,
            border_radius=8,
            bgcolor=t["surface"],
            content=ft.Row(
                controls=[
                    cover,
                    ft.Column(
                        expand=True,
                        spacing=0,
                        controls=[
                            ft.Text(book.title, size=14, weight=ft.FontWeight.BOLD, color=t["text"]),
                            ft.Text(subtitle, size=11, italic=True, color=t["text"]),
                        ],
                    ),
                    ft.IconButton(
                        icon=ft.Icons.STAR if book.favorite else ft.Icons.STAR_BORDER,
                        icon_color=t["favorite"],
                        tooltip="Favorite",
                        on_click=toggle_fav,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHECK_CIRCLE if book.read else ft.Icons.CHECK_CIRCLE_OUTLINE,
                        icon_color=t["accent"],
                        tooltip="Read / Unread",
                        on_click=toggle_read,
                    ),
                    ft.IconButton(icon=ft.Icons.EDIT, tooltip="Edit", on_click=edit),
                    ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Remove", on_click=remove),
                ],
            ),
        )

    # ---------------- collapsible tree (Authors tab) ----------------

    def build_tree(filtered_ids=None) -> ft.Control:
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

                book_column = ft.Column(
                    controls=[render_book_card(b) for b in books],
                    spacing=6,
                    visible=False,
                )

                def make_toggle(col=book_column):
                    def _toggle(e):
                        col.visible = not col.visible
                        e.control.icon = ft.Icons.EXPAND_LESS if col.visible else ft.Icons.EXPAND_MORE
                        page.update()
                    return _toggle

                header = ft.Row(
                    controls=[
                        ft.Icon(branch_icon, size=16, color=t["accent"]),
                        ft.Text(
                            branch_label if is_series else f"{branch_label} (standalone)",
                            size=13, color=t["text"],
                        ),
                        ft.Container(expand=True),
                        ft.IconButton(icon=ft.Icons.EXPAND_MORE, icon_size=18, on_click=make_toggle()),
                    ],
                )
                branch_controls.append(
                    ft.Container(
                        padding=ft.padding.only(left=14),
                        border=ft.border.only(left=ft.BorderSide(2, t["branch_line"])),
                        content=ft.Column(controls=[header, book_column], spacing=4),
                    )
                )

            if not branch_controls:
                continue

            author_column = ft.Column(controls=branch_controls, spacing=8, visible=False)

            def make_author_toggle(col=author_column):
                def _toggle(e):
                    col.visible = not col.visible
                    e.control.icon = ft.Icons.EXPAND_LESS if col.visible else ft.Icons.EXPAND_MORE
                    page.update()
                return _toggle

            author_header = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.PERSON_OUTLINE, color=t["accent"]),
                    ft.Text(author, size=16, weight=ft.FontWeight.BOLD, color=t["text"],
                            font_family="Vintage-Display"),
                    ft.Container(expand=True),
                    ft.IconButton(icon=ft.Icons.EXPAND_MORE, on_click=make_author_toggle()),
                ],
            )
            author_controls.append(
                ft.Container(
                    padding=10, border_radius=8, bgcolor=t["primary"],
                    content=ft.Column(controls=[author_header, author_column], spacing=6),
                )
            )

        if not author_controls:
            return ft.Container(
                padding=30,
                content=ft.Text("No books yet — import a file or add one manually.",
                                 italic=True, color=t["text"]),
            )

        return ft.Column(controls=author_controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    def build_flat_list(books) -> ft.Control:
        if not books:
            t = THEMES[state["theme"]]
            return ft.Container(padding=30, content=ft.Text("Nothing here yet.", italic=True, color=t["text"]))
        return ft.Column(
            controls=[render_book_card(b) for b in books],
            spacing=8, scroll=ft.ScrollMode.AUTO, expand=True,
        )

    def build_series_view() -> ft.Control:
        t = THEMES[state["theme"]]
        by_series = {}
        for b in library.all_books():
            if b.series:
                by_series.setdefault(b.series, []).append(b)
        if not by_series:
            return ft.Container(padding=30, content=ft.Text("No series tracked yet.", italic=True, color=t["text"]))
        blocks = []
        for series_name, books in sorted(by_series.items(), key=lambda kv: kv[0].lower()):
            books.sort(key=lambda x: (x.series_index is None, x.series_index or 0))
            blocks.append(
                ft.Container(
                    padding=10, border_radius=8, bgcolor=t["surface"],
                    content=ft.Column(controls=[
                        ft.Text(series_name, size=15, weight=ft.FontWeight.BOLD,
                                color=t["text"], font_family="Vintage-Display"),
                        ft.Column(controls=[render_book_card(b) for b in books], spacing=6),
                    ]),
                )
            )
        return ft.Column(controls=blocks, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------------- add / edit dialog ----------------

    def open_book_dialog(book: Book = None):
        editing = book is not None
        title_f = ft.TextField(label="Title", value=book.title if editing else "")
        author_f = ft.TextField(label="Author", value=book.author if editing else "")
        series_f = ft.TextField(label="Series (optional)", value=(book.series or "") if editing else "")
        series_idx_f = ft.TextField(
            label="# in series (optional)",
            value=str(book.series_index) if editing and book.series_index else "",
        )
        genre_f = ft.TextField(label="Genre (optional)", value=book.genre if editing else "")
        cover_f = ft.TextField(label="Cover image URL (optional)", value=book.cover_url if editing else "")

        def save(e):
            idx_val = None
            if series_idx_f.value.strip():
                try:
                    idx_val = float(series_idx_f.value.strip())
                except ValueError:
                    idx_val = None

            if editing:
                library.update_book(
                    book.id,
                    title=title_f.value.strip() or book.title,
                    author=author_f.value.strip() or "Unknown Author",
                    series=series_f.value.strip() or None,
                    series_index=idx_val,
                    genre=genre_f.value.strip(),
                    cover_url=cover_f.value.strip(),
                )
            else:
                library.add_book(Book(
                    title=title_f.value.strip(),
                    author=author_f.value.strip() or "Unknown Author",
                    series=series_f.value.strip() or None,
                    series_index=idx_val,
                    genre=genre_f.value.strip(),
                    cover_url=cover_f.value.strip(),
                    manual_entry=True,
                ))
            page.close(dialog)
            refresh_all()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Book" if editing else "Add Book Manually"),
            content=ft.Column(
                controls=[title_f, author_f, series_f, series_idx_f, genre_f, cover_f],
                tight=True, width=380, scroll=ft.ScrollMode.AUTO,
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
        imported_count = 0
        errors = []
        for f in e.files:
            try:
                new_books = import_file(f.path)
                for b in new_books:
                    library.add_book(b, persist=False)
                imported_count += len(new_books)
            except Exception as ex:
                errors.append(f"{f.name}: {ex}")
        library.save()
        refresh_all()
        msg = f"Imported {imported_count} book(s)."
        if errors:
            msg += " Some files had issues: " + "; ".join(errors)
        page.open(ft.SnackBar(ft.Text(msg)))

    file_picker.on_result = on_files_picked

    def open_import_picker(e):
        file_picker.pick_files(
            allow_multiple=True,
            allowed_extensions=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
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

    TAB_LABELS = ["Authors", "Series", "Read", "Unread", "Favorites"]
    tab_row = ft.Row(spacing=8)

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
                    state["selected_tab"] = idx
                    build_tab_row()
                    refresh_body()
                return _click

            buttons.append(
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=14, vertical=8),
                    border_radius=20,
                    bgcolor=t["accent"] if selected else ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
                    on_click=make_click(),
                    content=ft.Text(
                        label,
                        color=t["background"] if selected else t["text"],
                        weight=ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL,
                        size=13,
                    ),
                )
            )
        tab_row.controls = buttons

    def on_search_change(value):
        state["search_query"] = value
        refresh_body()

    def refresh_body():
        query = state["search_query"]
        idx = current_tab_index()

        if query:
            matched_ids = {b.id for b in library.search(query)}
            if idx == 0:
                content = build_tree(filtered_ids=matched_ids)
            elif idx == 1:
                content = build_flat_list([b for b in library.search(query) if b.series])
            elif idx == 2:
                content = build_flat_list([b for b in library.search(query) if b.read])
            elif idx == 3:
                content = build_flat_list([b for b in library.search(query) if not b.read])
            else:
                content = build_flat_list([b for b in library.search(query) if b.favorite])
        else:
            if idx == 0:
                content = build_tree()
            elif idx == 1:
                content = build_series_view()
            elif idx == 2:
                content = build_flat_list(library.read_books())
            elif idx == 3:
                content = build_flat_list(library.unread_books())
            else:
                content = build_flat_list(library.favorites())

        body_holder.content = ft.Column(
            controls=[content, ft.Divider(), lazy_load_trigger_row, lazy_load_column],
            expand=True, scroll=ft.ScrollMode.AUTO,
        )
        page.update()

    def refresh_all():
        refresh_body()

    state["selected_tab"] = 0
    build_tab_row()

    theme_dropdown = ft.Dropdown(
        label="Theme",
        value=DEFAULT_THEME,
        options=[ft.DropdownOption(key=name, text=name) for name in theme_names()],
        on_change=lambda e: (apply_theme(e.control.value), build_tab_row(), refresh_body()),
        width=200,
    )

    lazy_load_trigger = build_lazy_load_trigger(
        page=page,
        library=library,
        target_column=lazy_load_column,
        render_book_card=render_book_card,
        on_book_updated=lambda b: None,
    )
    lazy_load_trigger_row = ft.Row(controls=[lazy_load_trigger])

    header = ft.Container(
        padding=16,
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("StoryStrand", size=26, weight=ft.FontWeight.BOLD,
                                font_family="Vintage-Display"),
                        ft.Container(expand=True),
                        theme_dropdown,
                    ],
                ),
                ft.Row(
                    controls=[
                        search_field,
                        ft.ElevatedButton("Import File", icon=ft.Icons.UPLOAD_FILE, on_click=open_import_picker),
                        ft.ElevatedButton("Add Book", icon=ft.Icons.ADD, on_click=lambda e: open_book_dialog()),
                    ],
                ),
                tab_row,
            ],
        ),
    )

    page.add(
        ft.Column(
            controls=[header, body_holder],
            expand=True,
        )
    )

    apply_theme(DEFAULT_THEME)
    refresh_body()


if __name__ == "__main__":
    ft.app(target=main)

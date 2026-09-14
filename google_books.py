def _run_background_search(missing_books: List[Book], placeholder_index: dict):
    def _swap(book: Book):
        ghost = placeholder_index.get(book.id)
        if ghost in target_column.controls:
            idx = target_column.controls.index(ghost)
            target_column.controls[idx] = render_book_card(book)
        if on_book_updated:
            on_book_updated(book)
        page.update()

    def work(book: Book):
        meta = search_book_metadata(book.title, book.author)
        return book, meta

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(work, b) for b in missing_books]
        for future in as_completed(futures):
            book, meta = future.result()
            if meta:
                apply_metadata(book, meta)
            _swap(book)

    library.save()

"""Conservative display-only compaction; the extracted source stays intact."""


def _filled(value):
    # Zero, false, None/N/A, dashes and explicit unknowns are source values.
    return bool(str(value).translate({ord(c): None for c in "\u200b\u200c\u200d\ufeff"}).strip())


def compact(table):
    """Remove empty fields/sections and proven colspan duplicates, never guess values.

    TH cells label data; known two-column weapon templates also use TD labels.
    Unknown/unsupported layouts retain their original representation.
    """
    rows = table.get("rows", [])
    if not rows or not any(_filled(cell) for row in rows for cell in row):
        return None
    width = max(map(len, rows))
    flags = table.get("headers", [])
    if (table.get("unsupported") or len(flags) != len(rows)
            or any(len(row) != width for row in rows + flags)):
        return table
    origins = table.get("origins", [])
    has_origins = (len(origins) == len(rows)
                   and all(len(row) == width for row in origins))
    headings = [any(row) and all(flag or (has_origins and origins[i][c] is None)
                                for c, flag in enumerate(row))
                for i, row in enumerate(flags)]
    if all(headings):
        # All-TH markup may contain actual data, not just a header skeleton.
        return table
    key_value = table.get("kind") == "key_value" and width == 2
    payload = [i for i, row in enumerate(rows) if not headings[i] and any(
        _filled(cell) and not flags[i][c] and not (key_value and c == 0)
        for c, cell in enumerate(row))]
    if not payload:
        return None

    # Preserve the table's initial column headers/title, then retain a run of
    # section headings only when the following body block contains data.
    keep = set(payload)
    start = 0
    while start < len(rows) and headings[start]:
        start += 1
    # An initial full-width title is independent of the following section.
    if start and has_origins and origins[0][0] is not None and all(
            origin == origins[0][0] for origin in origins[0]):
        keep.add(0)
    for i in range(start):
        if not has_origins or len({tuple(o) for o in origins[i] if o is not None}) > 1:
            keep.add(i)
    pending = []
    active = False
    for i in range(len(rows)):
        if headings[i]:
            if not active:
                pending.append(i)
            else:
                pending = [i]
            active = False
        else:
            if i in keep:
                keep.update(pending)
            # A body block ends its preceding header run, including blank rows.
            active = True

    indices = sorted(keep)
    # Column headers alone do not make an otherwise empty column useful.
    columns = [c for c in range(width) if any(_filled(rows[i][c]) for i in payload)]
    if has_origins:
        retained = []
        for c in columns:
            # Equal text is NOT evidence of a merged cell. Only drop a column
            # if every surviving source row proves it shares the left origin.
            if retained and all(origins[i][c] is not None
                                and origins[i][c] == origins[i][retained[-1]]
                                for i in indices):
                continue
            retained.append(c)
        columns = retained
    result = dict(table)
    for key, matrix in (("rows", rows), ("headers", flags)):
        result[key] = [[matrix[i][c] for c in columns] for i in indices]
    if has_origins:
        result["origins"] = [[origins[i][c] for c in columns] for i in indices]
    return result


def display_cells(table, index):
    """Show a horizontally merged cell once; retain rowspan context on each row."""
    row = table["rows"][index]
    origins = table.get("origins", [])
    if index >= len(origins) or len(origins[index]) != len(row):
        return row
    seen = set()
    result = []
    for value, origin in zip(row, origins[index]):
        identity = tuple(origin) if origin is not None else None
        result.append("" if identity is not None and identity in seen else value)
        if identity is not None:
            seen.add(identity)
    return result

"""CSV downloads (audit logs, user records, analytics). Cells that a spreadsheet would read as a formula
(starting with ``=``, ``+``, ``-``, ``@``, tab or carriage return) are prefixed with an apostrophe, so an
exported value can never run as a formula when the file is opened."""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    """UTF-8 with a byte-order mark, so spreadsheet programs detect the encoding."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def sectioned_csv(sections: Sequence[tuple[str, Sequence[str], Iterable[Sequence[Any]]]]) -> bytes:
    """Several tables in one file, each preceded by its title and separated by an empty line."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for index, (title, header, rows) in enumerate(sections):
        if index:
            writer.writerow([])
        writer.writerow([safe_cell(title)])
        writer.writerow(header)
        for row in rows:
            writer.writerow([safe_cell(v) for v in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")

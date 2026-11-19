"""Reading an uploaded .xlsx file, for the student and routine imports.

openpyxl parses the file with defusedxml (a dependency), so XML tricks such as entity
declarations are refused. Whatever goes wrong while reading, a file that is not a zip, broken
XML or a refused entity, becomes one plain message: openpyxl's own messages are technical
("could not read worksheets from None") and would only confuse the person uploading.
"""
import itertools

import openpyxl
from flask import current_app

UNREADABLE_FILE_MESSAGE = "The file could not be read. Upload a valid CSV or XLSX file."


def xlsx_rows(stream, limit):
    """The first sheet's header row (None for an empty sheet) and up to `limit` + 1 rows after it,
    as tuples of cell values. Raises ValueError with a message for the user if the file cannot be read."""
    try:
        workbook = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        try:
            sheet = workbook.active or workbook.worksheets[0]
            values = sheet.iter_rows(values_only=True)
            header = next(values, None)
            rows = list(itertools.islice(values, limit + 1))
        finally:
            workbook.close()
    except Exception as exc:
        current_app.logger.warning("Could not read an uploaded spreadsheet (%s: %s)", type(exc).__name__, exc)
        raise ValueError(UNREADABLE_FILE_MESSAGE) from exc
    return header, rows

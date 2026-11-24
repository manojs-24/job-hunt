import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .providers.base import provider_names

COLUMNS = [
    ("title", "Title"), ("company", "Company"), ("location", "Location"),
    ("exp_required", "Experience required (yrs)"), ("salary", "Salary"),
    ("posted_at", "Posted"), ("source", "Source"), ("score", "Score"),
    ("reason", "Reason"), ("skills_matched", "Skills you match"),
    ("skills_missing", "Skills you lack"), ("found_by", "Found by"), ("saved", "Saved"), ("applied", "Applied"),
    ("url", "Apply link"),
]


def _cell(job: dict, key: str):
    value = job.get(key)
    if key in ("saved", "applied"):
        return "Yes" if value else ""
    if key == "found_by":
        return provider_names(value)
    return "" if value is None else value


def _row(job: dict) -> list:
    return [_cell(job, key) for key, _ in COLUMNS]


def to_csv(jobs: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for _, h in COLUMNS])
    writer.writerows(_row(j) for j in jobs)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8


def to_xlsx(jobs: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append([h for _, h in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for j in jobs:
        ws.append(_row(j))
    ws.freeze_panes = "A2"
    for i, (key, header) in enumerate(COLUMNS, start=1):
        longest = max([len(header)] + [len(str(_cell(j, key))) for j in jobs])
        ws.column_dimensions[get_column_letter(i)].width = min(longest + 2, 60)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

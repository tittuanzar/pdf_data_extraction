"""Excel workbook generation for extracted SV2 valve datasheets."""

from __future__ import annotations

import io

from openpyxl import Workbook

from .models import ParsedOutput


class ExcelWriter:
    """Builds a per-tag Excel workbook from a ``ParsedOutput``.

    Each extracted tag gets its own sheet named after its ``tag_no``.
    Columns: Sl.No | Feature name | Extracted feature value.
    """

    def build(self, parsed: ParsedOutput) -> io.BytesIO:
        wb = Workbook()

        if not parsed.tags:
            ws = wb.active
            ws.title = "tags extracted"
            ws.append(["Sl.No", "Feature name", "Extracted feature value"])
            return self._save(wb)

        wb.remove(wb.active)
        for tag in parsed.tags:
            sheet_name = str(tag.tag_no) or "Unknown"
            ws = wb.create_sheet(title=sheet_name[:31])
            ws.append(["Sl.No", "Feature name", "Extracted feature value"])

            for sl, (field_name, value) in enumerate(
                tag.model_dump().items(), start=1
            ):
                display_value = "" if value is None else value
                ws.append([sl, field_name, display_value])

        return self._save(wb)

    @staticmethod
    def _save(wb: Workbook) -> io.BytesIO:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

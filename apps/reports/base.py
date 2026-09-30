"""A report is plain data (titled sections of columns and rows) that renders to JSON, PDF, Excel or CSV."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Column:
    key: str
    label: str
    # text | money | number | percent | date | datetime, or auto: each row gives it under "kind".
    kind: str = "text"

    def kind_for(self, row: dict) -> str:
        return row.get("kind", "text") if self.kind == "auto" else self.kind


@dataclass
class Section:
    title: str
    columns: list[Column]
    rows: list[dict]
    # Values for some columns of a closing "total" row; the label goes in the first column.
    totals: dict | None = None
    # One line under the title (what is counted, what is left out).
    note: str = ""


@dataclass
class Report:
    key: str
    title: str
    # The period and the filters, in words.
    subtitle: str
    sections: list[Section] = field(default_factory=list)
    landscape: bool = True


def period_label(date_from: date, date_to: date, language: str) -> str:
    fmt = "%d/%m/%Y" if language == "fr" else "%d %b %Y"
    if date_from == date_to:
        return date_from.strftime(fmt)
    joiner = ("du {} au {}" if language == "fr" else "{} to {}").format(
        date_from.strftime(fmt), date_to.strftime(fmt)
    )
    return joiner[:1].upper() + joiner[1:]

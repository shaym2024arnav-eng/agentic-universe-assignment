"""Loads workflow lead schemas and the disposition catalogue."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize_header


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    default: str | None = None
    values: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass
class Schema:
    workflow_id: str
    fields: list[Field]
    header_map: dict[str, str] = field(default_factory=dict)  # normalised header -> field name

    @property
    def reference_field(self) -> str:
        return next(f.name for f in self.fields if f.reference)

    @property
    def phone_field(self) -> str:
        return next(f.name for f in self.fields if f.type == "phone")


@dataclass(frozen=True)
class Disposition:
    is_terminal: bool
    is_success: bool


def load_schemas(schema_dir: Path) -> dict[str, Schema]:
    out: dict[str, Schema] = {}
    for p in sorted(schema_dir.glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        fields = [
            Field(
                name=f["name"], type=f["type"], required=bool(f.get("required")),
                pii=bool(f.get("pii")), reference=bool(f.get("reference")),
                default=f.get("default"), values=tuple(f.get("values", ())),
                aliases=tuple(f.get("aliases", ())),
            )
            for f in raw["fields"]
        ]
        s = Schema(raw["workflow_id"], fields)
        for f in fields:
            for h in (f.name, *f.aliases):
                s.header_map.setdefault(normalize_header(h), f.name)
        out[s.workflow_id] = s
    return out


def load_dispositions(path: Path) -> dict[tuple[str, str], Disposition]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {
            (r["workflow_id"].strip(), r["code"].strip()): Disposition(
                r["is_terminal"].strip().lower() == "true",
                r["is_success"].strip().lower() == "true",
            )
            for r in csv.DictReader(fh)
        }

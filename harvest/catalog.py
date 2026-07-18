from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Catalog:
    raw: dict[str, Any]

    def source(self, name: str) -> dict[str, Any]:
        try:
            return self.raw["sources"][name]
        except KeyError as exc:
            raise CatalogError(f"unsupported source: {name}") from exc

    def dataset(self, source: str, dataset: str) -> dict[str, Any]:
        try:
            return self.source(source)["datasets"][dataset]
        except KeyError as exc:
            raise CatalogError(f"unsupported dataset for {source}: {dataset}") from exc

    def validate_request(self, source: str, dataset: str, start_year: int, end_year: int) -> dict[str, Any]:
        spec = self.dataset(source, dataset)
        if start_year > end_year:
            raise CatalogError("start year must not exceed end year")
        if start_year < int(spec["first_year"]) or end_year > int(spec["last_year"]):
            raise CatalogError(
                f"year range {start_year}-{end_year} outside supported range "
                f"{spec['first_year']}-{spec['last_year']}"
            )
        return spec


def load_catalog(path: Path) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise CatalogError("catalog must contain a sources mapping")
    return Catalog(data)


from __future__ import annotations

from . import nflcom, profootballarchives, statscrew

_ADAPTERS = {
    "nflcom": nflcom,
    "profootballarchives": profootballarchives,
    "statscrew": statscrew,
}


def get_adapter(source: str):
    try:
        return _ADAPTERS[source]
    except KeyError as exc:
        raise ValueError(f"unknown source adapter: {source}") from exc


from __future__ import annotations

import pytest

from harvest.http import Response
from harvest.pilot import _dataset_name, run_pilot


def _resp(html: str) -> Response:
    return Response("u", 200, "ok", html.encode(), "text/html")


def test_dataset_name_ignores_placeholder_segments():
    assert _dataset_name("/players/{letter}/{id}.html") == "players"
    assert _dataset_name("/drafts/{year}nfldraft.html") == "drafts"
    assert _dataset_name("/football/stats/p-{id}") == "football_stats"


def test_multi_table_pages_demand_table_tagging(tmp_path):
    """82 logical tables collapsed into 7 registry rows once already. A pilot that does
    not flag multi-table pages lets that happen again on a new source."""
    html = ("<table><tr><th>Player</th><th>Yds</th></tr><tr><td>A</td><td>1</td></tr></table>"
            "<table><tr><th>Transaction</th><th>Date</th></tr>"
            "<tr><td>Signed</td><td>1963</td></tr></table>")
    result = run_pilot(source="s", family="/players/{id}.html",
                       urls=["u1", "u2"], output_dir=tmp_path,
                       fetch=lambda u: _resp(html), artifact_run_id="t")
    assert result["registration_proposal"]["requires_table_tagging"] is True
    assert result["counters"]["distinct_table_shapes"] == 2
    assert "MULTI-TABLE" in result["registration_proposal"]["grain_note"]


def test_shape_stability_separates_structural_from_variant(tmp_path):
    """A shape on every page is structural; a rare one is a real variant or a page shape
    we have not understood. Both need a human -- neither may be averaged away."""
    common = "<table><tr><th>Year</th></tr><tr><td>1963</td></tr></table>"
    rare = "<table><tr><th>Snap Counts</th></tr><tr><td>7</td></tr></table>"
    pages = [common, common, common, common + rare]
    calls = iter(pages)
    result = run_pilot(source="s", family="/p/{id}.html",
                       urls=["a", "b", "c", "d"], output_dir=tmp_path,
                       fetch=lambda u: _resp(next(calls)), artifact_run_id="t")
    by_stability = {s["stability"] for s in result["table_shapes"]}
    assert "structural" in by_stability and "variant" in by_stability


def test_source_native_ids_are_detected_because_names_are_the_twins_hazard(tmp_path):
    html = ('<table><tr><th>Player</th></tr><tr><td>'
            '<a href="/football/stats/p-brownjim001">Jim Brown</a></td></tr></table>')
    result = run_pilot(source="s", family="/x/{id}", urls=["u"], output_dir=tmp_path,
                       fetch=lambda u: _resp(html), artifact_run_id="t")
    shape = result["table_shapes"][0]
    assert shape["has_source_native_id"] is True
    assert result["registration_proposal"]["joinable"] is True


def test_pilot_writes_a_reviewable_proposal_and_manifest(tmp_path):
    html = "<table><tr><th>Year</th></tr><tr><td>1963</td></tr></table>"
    run_pilot(source="s", family="/y/{id}", urls=["u"], output_dir=tmp_path,
              fetch=lambda u: _resp(html), artifact_run_id="t")
    assert (tmp_path / "PILOT.json").is_file()
    assert (tmp_path / "ARTIFACT_MANIFEST.json").is_file()

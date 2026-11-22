"""Tests for U1 Route Cards UI rendering requirements (Sprint 2 - U1)."""

from __future__ import annotations

from aeropath.api.map_ui import HTML_PAGE


def test_map_ui_renders_u1_route_card_specifications() -> None:
    # U1 requires on each card: AQI category with text, time, distance,
    # dose savings percent, confidence, data age
    assert "route-tab-card" in HTML_PAGE
    assert "dose-savings-badge" in HTML_PAGE or "Less Dose" in HTML_PAGE
    assert "resCatBadge" in HTML_PAGE
    assert "data-age" in HTML_PAGE or "dataAge" in HTML_PAGE or "Data Age" in HTML_PAGE
    assert "r.confidence" in HTML_PAGE or "Conf:" in HTML_PAGE

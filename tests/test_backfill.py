"""
Tests for backfill.py — specifically the multi-year worksheet routing,
which is the actual new behavior. Mocks pco_get and gspread; nothing
here touches a real API.
"""

from unittest.mock import patch

import backfill
from tests.fakes import FakeSpreadsheet, FakeWorksheet, patch_gspread


def make_plan(plan_id, sort_date_iso, dates_label=""):
    return {"id": plan_id, "attributes": {"sort_date": sort_date_iso, "dates": dates_label or sort_date_iso}}


def make_items(song_titles):
    return {"data": [{"attributes": {"item_type": "song", "title": t}} for t in song_titles]}


def test_plans_from_different_years_go_to_different_worksheets(monkeypatch):
    spreadsheet = FakeSpreadsheet({"Run Log": FakeWorksheet([])})
    patch_gspread(monkeypatch, backfill, spreadsheet)

    plan_2025 = make_plan("p1", "2025-08-24T09:00:00Z")
    plan_2026 = make_plan("p2", "2026-08-30T09:00:00Z")

    def fake_pco_get(url, params=None):
        if "/items" not in url:
            return {"data": [plan_2025, plan_2026], "links": {}}
        if "/plans/p1/items" in url:
            return make_items(["2025 Song"])
        if "/plans/p2/items" in url:
            return make_items(["2026 Song"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("backfill.pco_get", side_effect=fake_pco_get):
        summary = backfill.run_backfill(service_type_id="fake_service_type")

    assert summary["plans_processed"] == 2
    assert summary["created"] == 2
    assert spreadsheet.added == ["2025", "2026"]  # created in chronological order

    ws_2025 = spreadsheet.worksheet("2025")
    ws_2026 = spreadsheet.worksheet("2026")
    assert ws_2025.rows[0] == "2025 Song Bank"
    assert ws_2025.rows[-1] == "2025 Song 8/24"
    assert ws_2026.rows[0] == "2026 Song Bank"
    assert ws_2026.rows[-1] == "2026 Song 8/30"


def test_reuses_same_year_worksheet_across_multiple_plans_in_that_year(monkeypatch):
    spreadsheet = FakeSpreadsheet({"Run Log": FakeWorksheet([])})
    patch_gspread(monkeypatch, backfill, spreadsheet)

    plan_a = make_plan("p1", "2026-01-04T09:00:00Z")
    plan_b = make_plan("p2", "2026-01-11T09:00:00Z")

    def fake_pco_get(url, params=None):
        if "/items" not in url:
            return {"data": [plan_a, plan_b], "links": {}}
        if "/plans/p1/items" in url:
            return make_items(["Song A"])
        if "/plans/p2/items" in url:
            return make_items(["Song B"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("backfill.pco_get", side_effect=fake_pco_get):
        summary = backfill.run_backfill(service_type_id="fake_service_type")

    assert summary["plans_processed"] == 2
    assert summary["created"] == 2
    assert spreadsheet.added == ["2026"]  # only created once, reused for the second plan

    ws_2026 = spreadsheet.worksheet("2026")
    assert "Song A 1/4" in ws_2026.rows
    assert "Song B 1/11" in ws_2026.rows


def test_date_range_filtering_still_works(monkeypatch):
    spreadsheet = FakeSpreadsheet({})
    patch_gspread(monkeypatch, backfill, spreadsheet)

    too_early = make_plan("p1", "2026-01-01T09:00:00Z")
    in_range = make_plan("p2", "2026-06-01T09:00:00Z")
    too_late = make_plan("p3", "2026-12-01T09:00:00Z")

    def fake_pco_get(url, params=None):
        if "/items" not in url:
            return {"data": [too_early, in_range, too_late], "links": {}}
        if "/plans/p2/items" in url:
            return make_items(["In Range Song"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("backfill.pco_get", side_effect=fake_pco_get):
        summary = backfill.run_backfill(
            start_date="2026-03-01", end_date="2026-09-01", service_type_id="fake_service_type"
        )

    assert summary["plans_processed"] == 1
    assert summary["created"] == 1


def test_no_matching_plans_returns_zeroed_summary_without_error(monkeypatch):
    spreadsheet = FakeSpreadsheet({})
    patch_gspread(monkeypatch, backfill, spreadsheet)

    def fake_pco_get(url, params=None):
        return {"data": [], "links": {}}

    with patch("backfill.pco_get", side_effect=fake_pco_get):
        summary = backfill.run_backfill(service_type_id="fake_service_type")

    assert summary == {"plans_processed": 0, "created": 0, "updated": 0, "skipped": 0}
    assert spreadsheet.added == []
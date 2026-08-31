"""
Unit tests for get_latest_plan_and_songs() — mocks pco_get entirely,
so these never touch the real Planning Center API.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

import pco_script


def make_plan(plan_id, sort_date_iso, dates_label="Test Date"):
    return {"id": plan_id, "attributes": {"sort_date": sort_date_iso, "dates": dates_label}}


def make_items(song_titles, non_song_titles=None):
    data = [{"attributes": {"item_type": "song", "title": t}} for t in song_titles]
    for t in non_song_titles or []:
        data.append({"attributes": {"item_type": "header", "title": t}})
    return {"data": data}


def test_picks_most_recent_past_plan_not_future():
    today = date.today()
    past_plan = make_plan("plan_past", (today - timedelta(days=7)).isoformat() + "T09:00:00Z")
    future_plan = make_plan("plan_future", (today + timedelta(days=7)).isoformat() + "T09:00:00Z")

    def fake_pco_get(url, params=None):
        if url.endswith("/plans"):
            if params and params.get("filter") == "past":
                return {"data": [past_plan]}
            return {"data": [past_plan, future_plan]}
        if url.endswith("/items"):
            return make_items(["Song A", "Song B"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("pco_script.pco_get", side_effect=fake_pco_get):
        plan, songs, sing_date = pco_script.get_latest_plan_and_songs("fake_service_type")

    assert plan["id"] == "plan_past"
    assert songs == ["Song A", "Song B"]


def test_falls_back_when_past_filter_returns_empty():
    today = date.today()
    past_plan = make_plan("plan_past", (today - timedelta(days=1)).isoformat() + "T09:00:00Z")
    calls = []

    def fake_pco_get(url, params=None):
        calls.append(params)
        if url.endswith("/plans"):
            if params and params.get("filter") == "past":
                return {"data": []}
            return {"data": [past_plan]}
        if url.endswith("/items"):
            return make_items(["Song A"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("pco_script.pco_get", side_effect=fake_pco_get):
        plan, songs, sing_date = pco_script.get_latest_plan_and_songs("fake_service_type")

    assert plan["id"] == "plan_past"
    assert any(p and p.get("filter") == "past" for p in calls)


def test_raises_when_no_plans_found_at_all():
    def fake_pco_get(url, params=None):
        return {"data": []}

    with patch("pco_script.pco_get", side_effect=fake_pco_get):
        with pytest.raises(RuntimeError):
            pco_script.get_latest_plan_and_songs("fake_service_type")


def test_filters_out_non_song_items():
    today = date.today()
    plan = make_plan("plan_1", (today - timedelta(days=1)).isoformat() + "T09:00:00Z")

    def fake_pco_get(url, params=None):
        if url.endswith("/plans"):
            return {"data": [plan]}
        if url.endswith("/items"):
            return make_items(["Actual Song"], non_song_titles=["Welcome", "Announcements"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("pco_script.pco_get", side_effect=fake_pco_get):
        _, songs, _ = pco_script.get_latest_plan_and_songs("fake_service_type")

    assert songs == ["Actual Song"]


def test_sing_date_formatted_as_month_slash_day():
    plan = make_plan("plan_1", "2026-08-30T09:00:00Z")

    def fake_pco_get(url, params=None):
        if url.endswith("/plans"):
            return {"data": [plan]}
        if url.endswith("/items"):
            return make_items(["Song A"])
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("pco_script.pco_get", side_effect=fake_pco_get):
        _, _, sing_date = pco_script.get_latest_plan_and_songs("fake_service_type")

    assert sing_date == "8/30"
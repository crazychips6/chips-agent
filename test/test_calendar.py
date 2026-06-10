"""Test calendar tool — 日期时间查询与计算"""
import json

from tool.builtins.calendar_tool import _handle


class TestCalendarNow:
    def test_now_returns_all_fields(self):
        data = json.loads(_handle({"action": "now"}))
        assert "datetime" in data
        assert "date" in data
        assert "time" in data
        assert "timezone" in data
        assert "weekday" in data
        assert "iso_weekday" in data
        assert 1 <= data["iso_weekday"] <= 7
        assert data["year"] >= 2020
        assert 1 <= data["month"] <= 12
        assert 1 <= data["day"] <= 31


class TestCalendarMonth:
    def test_current_month(self):
        data = json.loads(_handle({"action": "month"}))
        assert "days_in_month" in data
        assert 28 <= data["days_in_month"] <= 31
        assert 0 <= data["first_weekday"] <= 6
        assert "calendar_text" in data

    def test_specific_month(self):
        data = json.loads(_handle({"action": "month", "year": 2026, "month": 6}))
        assert data["year"] == 2026
        assert data["month"] == 6
        assert data["days_in_month"] == 30

    def test_february_leap(self):
        data = json.loads(_handle({"action": "month", "year": 2024, "month": 2}))
        assert data["days_in_month"] == 29

    def test_february_non_leap(self):
        data = json.loads(_handle({"action": "month", "year": 2023, "month": 2}))
        assert data["days_in_month"] == 28

    def test_invalid_month(self):
        data = json.loads(_handle({"action": "month", "month": 13}))
        assert "error" in data


class TestCalendarDiff:
    def test_same_day(self):
        data = json.loads(_handle({"action": "diff", "from": "2026-06-11", "to": "2026-06-11"}))
        assert data["days"] == 0

    def test_future(self):
        data = json.loads(_handle({"action": "diff", "from": "2026-06-01", "to": "2026-06-11"}))
        assert data["days"] == 10

    def test_past(self):
        data = json.loads(_handle({"action": "diff", "from": "2026-06-11", "to": "2026-06-01"}))
        assert data["days"] == -10

    def test_missing_params(self):
        data = json.loads(_handle({"action": "diff", "from": "2026-06-01"}))
        assert "error" in data

    def test_invalid_date(self):
        data = json.loads(_handle({"action": "diff", "from": "abc", "to": "def"}))
        assert "error" in data


class TestCalendarWeekday:
    def test_monday(self):
        data = json.loads(_handle({"action": "weekday", "date": "2026-06-01"}))
        assert data["weekday"] == "Monday"
        assert data["iso_weekday"] == 1
        assert data["is_weekend"] is False

    def test_sunday(self):
        data = json.loads(_handle({"action": "weekday", "date": "2026-06-07"}))
        assert data["weekday"] == "Sunday"
        assert data["iso_weekday"] == 7
        assert data["is_weekend"] is True

    def test_missing_date(self):
        data = json.loads(_handle({"action": "weekday"}))
        assert "error" in data


class TestCalendarAdd:
    def test_add_days(self):
        data = json.loads(_handle({"action": "add", "date": "2026-06-01", "days": 10}))
        assert data["result_date"] == "2026-06-11"

    def test_subtract_days(self):
        data = json.loads(_handle({"action": "add", "date": "2026-06-11", "days": -10}))
        assert data["result_date"] == "2026-06-01"

    def test_zero_days(self):
        data = json.loads(_handle({"action": "add", "date": "2026-06-11", "days": 0}))
        assert data["result_date"] == "2026-06-11"

    def test_cross_month(self):
        data = json.loads(_handle({"action": "add", "date": "2026-06-28", "days": 5}))
        assert data["result_date"] == "2026-07-03"

    def test_cross_year(self):
        data = json.loads(_handle({"action": "add", "date": "2026-12-30", "days": 5}))
        assert data["result_date"] == "2027-01-04"

    def test_missing_date(self):
        data = json.loads(_handle({"action": "add", "days": 10}))
        assert "error" in data

    def test_missing_days(self):
        data = json.loads(_handle({"action": "add", "date": "2026-06-11"}))
        assert data["result_date"] == "2026-06-11"  # days 默认为 0


class TestUnknownAction:
    def test_unknown(self):
        data = json.loads(_handle({"action": "unknown"}))
        assert "error" in data

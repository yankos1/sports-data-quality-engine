from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from src.main import build_parser
import src.main as main_module
from src.scrapers.live_odds_scraper import LiveOddsScraper, SUPPORTED_LEAGUES


@pytest.mark.parametrize("league", ["premier-league", "super-league-2"])
def test_scraper_uses_url_for_selected_league(league):
    scraper = LiveOddsScraper(league=league)

    assert scraper.source_url == SUPPORTED_LEAGUES[league]


def test_scraper_defaults_to_super_league_two_and_four_days():
    scraper = LiveOddsScraper()

    assert scraper.league == "super-league-2"
    assert scraper.max_days_ahead == 4
    assert scraper.source_url == SUPPORTED_LEAGUES["super-league-2"]


def test_kickoff_beyond_max_days_ahead_is_outside_collection_window():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    cutoff_date = now + timedelta(days=4)
    too_late = cutoff_date + timedelta(minutes=1)

    assert not LiveOddsScraper._is_within_window(too_late, now, cutoff_date)
    assert LiveOddsScraper._is_within_window(cutoff_date, now, cutoff_date)


def test_closed_or_incomplete_market_is_not_active():
    assert not LiveOddsScraper._has_active_market([None, None, None])
    assert not LiveOddsScraper._has_active_market([2.1, None, 3.2])
    assert not LiveOddsScraper._has_active_market([None, 0.0, None])
    assert LiveOddsScraper._has_active_market([2.1, 3.2, 4.0])


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Tomorrow 15:00", datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)),
        ("Today 20:45", datetime(2026, 9, 26, 20, 45, tzinfo=timezone.utc)),
        ("28.09. 15:00", datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)),
    ],
)
def test_parse_kickoff_supports_relative_and_absolute_dates(label, expected):
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    assert LiveOddsScraper._parse_kickoff([label], now=now) == expected


def test_scraper_can_inherit_last_valid_kickoff_for_grouped_match():
    last_kickoff = LiveOddsScraper._parse_kickoff(
        ["Tomorrow 15:00"],
        now=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
    )

    inherited_kickoff = LiveOddsScraper._parse_kickoff(["Home - Away"])
    if inherited_kickoff is None:
        inherited_kickoff = last_kickoff

    assert inherited_kickoff == datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def test_cli_exposes_league_and_days_ahead_options():
    parser = build_parser()

    defaults = parser.parse_args([])
    configured = parser.parse_args(
        ["--league", "premier-league", "--days-ahead", "7"]
    )

    assert defaults.league == "super-league-2"
    assert defaults.days_ahead == 4
    assert configured.league == "premier-league"
    assert configured.days_ahead == 7

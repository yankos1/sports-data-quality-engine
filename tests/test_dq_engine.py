import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.validation.dq_engine as dq_engine_module
from src.database.connection import migrate_clean_odds_history_constraint
from src.database.models import Base, CleanOdds, DQException, Fixture, Team
from src.validation.dq_engine import DQEngine, DQ_RULE_CODES


def test_clean_odds_has_history_unique_key_and_utc_python_default():
    history_constraint = next(
        constraint
        for constraint in CleanOdds.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )

    assert history_constraint.name == "uq_clean_odds_history"
    assert [column.name for column in history_constraint.columns] == [
        "fixture_id",
        "bookmaker",
        "captured_at",
    ]
    assert CleanOdds.captured_at.property.columns[0].default.arg.__name__ == "utcnow"


def test_history_constraint_migration_is_idempotent_for_current_schema():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    migrate_clean_odds_history_constraint(engine)

    engine.dispose()


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False)

    with test_session_factory() as session:
        session.add_all([Team(canonical_name="Man City"), Team(canonical_name="Ipswich")])
        session.commit()

    monkeypatch.setattr(dq_engine_module, "SessionLocal", test_session_factory)
    yield test_session_factory
    engine.dispose()


def _fixture_data(**overrides):
    fixture_data = {
        "competition": "Premier League",
        "kickoff_at": "2026-10-10T15:00:00Z",
        "raw_home_team": "Man City",
        "raw_away_team": "Ipswich",
        "bookmaker": "Test bookmaker",
        "odds_home": 1.50,
        "odds_draw": 4.20,
        "odds_away": 6.50,
    }
    fixture_data.update(overrides)
    return fixture_data


def _run_pipeline(tmp_path, fixture_data):
    raw_file = tmp_path / "raw_odds.json"
    raw_file.write_text(
        json.dumps(
            {
                "scraped_at": "2026-09-26T12:00:00Z",
                "fixtures": fixture_data if isinstance(fixture_data, list) else [fixture_data],
            }
        ),
        encoding="utf-8",
    )
    return DQEngine().run_pipeline(raw_file)


def _assert_only_rejection(metrics, expected_rule):
    assert set(metrics["rejected_by_rule"]) == set(DQ_RULE_CODES)
    assert metrics["rejected_by_rule"][expected_rule] == 1
    assert sum(metrics["rejected_by_rule"].values()) == 1


def test_unopened_market_is_classified_as_information(tmp_path, session_factory):
    metrics = _run_pipeline(
        tmp_path,
        _fixture_data(odds_home=None, odds_draw=None, odds_away=None),
    )

    _assert_only_rejection(metrics, "DQ_INFO_UNOPENED_MARKET")


@pytest.mark.parametrize("invalid_odd", [1.00, -1.00])
def test_odds_at_or_below_one_are_invalid(tmp_path, session_factory, invalid_odd):
    metrics = _run_pipeline(tmp_path, _fixture_data(odds_home=invalid_odd))

    _assert_only_rejection(metrics, "DQ_ERR_INVALID_ODDS")


@pytest.mark.parametrize(
    ("odds_home", "odds_draw", "odds_away"),
    [
        (10.00, 10.00, 10.00),
        (1.50, 2.00, 2.00),
    ],
)
def test_out_of_range_overround_is_suspected_permutation(
    tmp_path, session_factory, odds_home, odds_draw, odds_away
):
    metrics = _run_pipeline(
        tmp_path,
        _fixture_data(
            odds_home=odds_home,
            odds_draw=odds_draw,
            odds_away=odds_away,
        ),
    )

    _assert_only_rejection(metrics, "DQ_ERR_SUSPECT_PERMUTATION")


def test_abnormally_low_draw_odd_is_suspected_permutation(
    tmp_path, session_factory
):
    metrics = _run_pipeline(
        tmp_path,
        _fixture_data(odds_home=1.50, odds_draw=1.22, odds_away=6.50),
    )

    _assert_only_rejection(metrics, "DQ_ERR_SUSPECT_PERMUTATION")
    with session_factory() as session:
        rejection = session.scalar(select(DQException))

    assert "Cote du nul suspecte" in rejection.message


def test_unknown_team_is_classified_as_unresolved(tmp_path, session_factory):
    metrics = _run_pipeline(
        tmp_path,
        _fixture_data(raw_home_team="FC Fake Team"),
    )

    _assert_only_rejection(metrics, "DQ_ERR_UNRESOLVED_TEAM")


def test_valid_odds_are_persisted_without_exception(tmp_path, session_factory):
    metrics = _run_pipeline(tmp_path, _fixture_data())

    assert metrics["odds_persisted"] == 1
    assert metrics["rejected_total"] == 0
    assert all(count == 0 for count in metrics["rejected_by_rule"].values())
    with session_factory() as session:
        clean_odds = session.scalars(select(CleanOdds)).all()
        exceptions = session.scalars(select(DQException)).all()

    assert len(clean_odds) == 1
    assert len(exceptions) == 0


@pytest.mark.parametrize(
    ("previous_capture", "expected_persisted", "expected_skipped"),
    [
        (datetime(2026, 9, 26, 11, 50), 0, 1),
        (datetime(2026, 9, 26, 11, 45), 1, 0),
    ],
)
def test_identical_snapshot_deduplication_uses_strict_fifteen_minute_window(
    tmp_path,
    session_factory,
    previous_capture,
    expected_persisted,
    expected_skipped,
):
    with session_factory() as session:
        home_team_id = session.scalar(
            select(Team.id).where(Team.canonical_name == "Man City")
        )
        away_team_id = session.scalar(
            select(Team.id).where(Team.canonical_name == "Ipswich")
        )
        fixture = Fixture(
            competition="Premier League",
            kickoff_at=datetime(2026, 10, 10, 15, 0),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
        session.add(fixture)
        session.flush()
        session.add(
            CleanOdds(
                fixture_id=fixture.id,
                bookmaker="Test bookmaker",
                odds_home=Decimal("1.50"),
                odds_draw=Decimal("4.20"),
                odds_away=Decimal("6.50"),
                captured_at=previous_capture,
            )
        )
        session.commit()

    metrics = _run_pipeline(tmp_path, _fixture_data())

    assert metrics["odds_persisted"] == expected_persisted
    assert metrics["snapshots_skipped_duplicates"] == expected_skipped
    with session_factory() as session:
        snapshots = session.scalars(select(CleanOdds)).all()

    assert len(snapshots) == 1 + expected_persisted


def test_statistical_warning_does_not_block_valid_odds(tmp_path, session_factory):
    with session_factory() as session:
        home_team_id = session.scalar(
            select(Team.id).where(Team.canonical_name == "Man City")
        )
        away_team_id = session.scalar(
            select(Team.id).where(Team.canonical_name == "Ipswich")
        )
        fixture = Fixture(
            competition="Premier League",
            kickoff_at=datetime(2026, 10, 10, 15, 0),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
        session.add(fixture)
        session.flush()
        history_odds = [
            ("1.60", "3.85", "4.50"),
            ("1.62", "3.80", "4.50"),
            ("1.61", "3.82", "4.50"),
            ("1.60", "3.80", "4.55"),
            ("1.63", "3.75", "4.50"),
            ("1.61", "3.85", "4.45"),
            ("1.62", "3.78", "4.55"),
            ("1.60", "3.88", "4.48"),
            ("1.64", "3.75", "4.50"),
            ("1.61", "3.80", "4.52"),
        ]
        session.add_all(
            CleanOdds(
                fixture_id=fixture.id,
                bookmaker="Historical bookmaker",
                odds_home=Decimal(home),
                odds_draw=Decimal(draw),
                odds_away=Decimal(away),
                captured_at=datetime(2026, 9, 25) + timedelta(minutes=index),
            )
            for index, (home, draw, away) in enumerate(history_odds)
        )
        session.commit()

    metrics = _run_pipeline(
        tmp_path,
        _fixture_data(odds_home=1.60, odds_draw=4.00, odds_away=4.00),
    )

    assert metrics["odds_persisted"] == 1
    assert metrics["rejected_total"] == 0
    assert metrics["warnings_by_rule"]["DQ_WARN_STATISTICAL_OUTLIER"] == 1
    with session_factory() as session:
        clean_odds = session.scalars(select(CleanOdds)).all()
        warning = session.scalar(
            select(DQException).where(
                DQException.rule_code == "DQ_WARN_STATISTICAL_OUTLIER"
            )
        )

    assert len(clean_odds) == 11
    assert warning is not None
    assert warning.fixture_id is not None
    assert "112.50%" in warning.message


def test_cross_bookmaker_warning_does_not_block_valid_odds(tmp_path, session_factory):
    fixtures = [
        _fixture_data(bookmaker="Book A", odds_home=2.10, odds_draw=3.40, odds_away=3.40),
        _fixture_data(bookmaker="Book B", odds_home=2.15, odds_draw=3.40, odds_away=3.40),
        _fixture_data(bookmaker="Book C", odds_home=2.05, odds_draw=3.40, odds_away=3.40),
        _fixture_data(bookmaker="Book D", odds_home=3.40, odds_draw=2.60, odds_away=2.60),
    ]

    metrics = _run_pipeline(tmp_path, fixtures)

    assert metrics["odds_persisted"] == 4
    assert metrics["rejected_total"] == 0
    assert metrics["warnings_by_rule"]["DQ_WARN_CROSS_BOOKMAKER_OUTLIER"] == 1
    with session_factory() as session:
        clean_odds = session.scalars(select(CleanOdds)).all()
        warning = session.scalar(
            select(DQException).where(
                DQException.rule_code == "DQ_WARN_CROSS_BOOKMAKER_OUTLIER"
            )
        )

    assert len(clean_odds) == 4
    assert warning is not None
    assert "écart relatif=61.9%" in warning.message
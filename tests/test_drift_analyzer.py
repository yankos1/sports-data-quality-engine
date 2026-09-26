from datetime import datetime
from decimal import Decimal

import pytest
import pandas as pd
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.reporting.drift_analyzer as drift_analyzer_module
from src.database.models import Base, CleanOdds, Fixture, Team
from src.reporting.drift_analyzer import (
    MarketDriftAnalyzer,
    calculate_fair_odds,
    calculate_fair_probabilities,
)
from src.reporting.excel_exporter import ExcelReportGenerator


@pytest.fixture
def drift_database(monkeypatch):
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)
    with test_session_factory() as session:
        home_team = Team(canonical_name="Arsenal")
        away_team = Team(canonical_name="Chelsea")
        session.add_all([home_team, away_team])
        session.flush()
        fixture = Fixture(
            competition="Premier League",
            kickoff_at=datetime(2026, 10, 10, 15, 0),
            home_team_id=home_team.id,
            away_team_id=away_team.id,
        )
        session.add(fixture)
        session.flush()
        session.add_all(
            [
                CleanOdds(
                    fixture_id=fixture.id,
                    bookmaker="Test bookmaker",
                    odds_home=Decimal("2.00"),
                    odds_draw=Decimal("3.50"),
                    odds_away=Decimal("4.00"),
                    captured_at=datetime(2026, 9, 26, 9, 0),
                ),
                CleanOdds(
                    fixture_id=fixture.id,
                    bookmaker="Test bookmaker",
                    odds_home=Decimal("1.80"),
                    odds_draw=Decimal("3.50"),
                    odds_away=Decimal("4.00"),
                    captured_at=datetime(2026, 9, 26, 10, 0),
                ),
                CleanOdds(
                    fixture_id=fixture.id,
                    bookmaker="Test bookmaker",
                    odds_home=Decimal("1.98"),
                    odds_draw=Decimal("3.50"),
                    odds_away=Decimal("4.00"),
                    captured_at=datetime(2026, 9, 26, 11, 0),
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(drift_analyzer_module, "engine", test_engine)
    yield
    test_engine.dispose()


def test_detects_ten_percent_home_odds_drop(drift_database):
    drifts = MarketDriftAnalyzer().get_significant_drifts()

    home_drift = drifts.loc[
        (drifts["Issue"] == "1")
        & ((drifts["Nouvelle cote"] - 1.80).abs() < 1e-9)
    ].iloc[0]
    assert home_drift["Ancienne cote"] == pytest.approx(2.00)
    assert home_drift["Nouvelle cote"] == pytest.approx(1.80)
    assert home_drift["Variation (%)"] == pytest.approx(-10.0)
    assert str(home_drift["Date snapshot précédent"]).startswith("2026-09-26 09:00:00")
    assert str(home_drift["Date snapshot actuel"]).startswith("2026-09-26 10:00:00")
    old_probability = calculate_fair_probabilities(2.00, 3.50, 4.00)[0]
    new_probability = calculate_fair_probabilities(1.80, 3.50, 4.00)[0]
    assert home_drift["Ancienne proba (%)"] == pytest.approx(old_probability)
    assert home_drift["Nouvelle proba (%)"] == pytest.approx(new_probability)
    assert home_drift["Gain de proba (pts)"] == pytest.approx(
        new_probability - old_probability
    )
    assert home_drift["Cote Fair actuelle"] == pytest.approx(
        calculate_fair_odds(1.80, 3.50, 4.00)[0]
    )


def test_detects_bearish_sharp_reversal(drift_database):
    drifts = MarketDriftAnalyzer().get_significant_drifts()
    reversal = drifts.loc[
        (drifts["Issue"] == "1")
        & ((drifts["Nouvelle cote"] - 1.98).abs() < 1e-9)
    ].iloc[0]

    assert reversal["Variation (%)"] == pytest.approx(10.0)
    assert reversal["Signal Marché"] == "SHARP REVERSAL (BEARISH)"
    assert reversal["Reversal Intensity (%)"] == pytest.approx(20.0)


def test_balanced_market_fair_probabilities_sum_to_one():
    probabilities = calculate_fair_probabilities(2.10, 3.40, 3.40)

    assert sum(probabilities) == pytest.approx(1.0, abs=1e-6)


def test_excel_report_exports_and_formats_market_drift(tmp_path, monkeypatch):
    drift_columns = [
        "Date match",
        "Domicile",
        "Extérieur",
        "Issue",
        "Ancienne cote",
        "Nouvelle cote",
        "Variation (%)",
        "Date snapshot précédent",
        "Date snapshot actuel",
        "Ancienne proba (%)",
        "Nouvelle proba (%)",
        "Gain de proba (pts)",
        "Cote Fair actuelle",
        "Signal Marché",
        "Reversal Intensity (%)",
    ]
    drift_data = pd.DataFrame(
        [[
            "2026-10-10",
            "Arsenal",
            "Chelsea",
            "1",
            2.0,
            1.8,
            -10.0,
            "before",
            "after",
            0.48,
            0.50,
            0.02,
            2.00,
            "SHARP REVERSAL (BEARISH)",
            20.0,
        ]],
        columns=drift_columns,
    )
    monkeypatch.setattr(
        ExcelReportGenerator,
        "_load_dataframes",
        staticmethod(
            lambda: (pd.DataFrame(), pd.DataFrame(), drift_data)
        ),
    )
    output_path = tmp_path / "drift-report.xlsx"

    ExcelReportGenerator().generate_report(output_path)

    workbook = load_workbook(output_path)
    worksheet = workbook["Market Trends & Drift"]
    assert worksheet["G2"].value == pytest.approx(-0.1)
    assert worksheet["G2"].number_format == "+0.00%;-0.00%"
    assert worksheet["J2"].number_format == "0.00%"
    assert worksheet["K2"].number_format == "0.00%"
    assert worksheet["L2"].number_format == "+0.00%;-0.00%"
    assert worksheet["M2"].number_format == "0.00"
    assert worksheet["O2"].number_format == '0.00"%"'
    assert worksheet["N2"].value == "SHARP REVERSAL (BEARISH)"
    assert len(worksheet.conditional_formatting) == 2
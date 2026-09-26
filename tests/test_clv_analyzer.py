from datetime import datetime
from decimal import Decimal

import pytest
import pandas as pd
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.reporting.clv_analyzer as clv_analyzer_module
from src.database.models import Base, CleanOdds, Fixture, Team
from src.reporting.clv_analyzer import CLVAnalyzer
from src.reporting.excel_exporter import ExcelReportGenerator


@pytest.fixture
def historical_clv_database(monkeypatch):
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
        kickoff_at = datetime(2025, 9, 27, 15, 0)
        fixture = Fixture(
            competition="Premier League",
            kickoff_at=kickoff_at,
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
                    odds_home=Decimal("2.20"),
                    odds_draw=Decimal("3.40"),
                    odds_away=Decimal("3.60"),
                    captured_at=datetime(2025, 9, 26, 15, 0),
                ),
                CleanOdds(
                    fixture_id=fixture.id,
                    bookmaker="Test bookmaker",
                    odds_home=Decimal("1.90"),
                    odds_draw=Decimal("3.50"),
                    odds_away=Decimal("3.70"),
                    captured_at=datetime(2025, 9, 27, 14, 30),
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(clv_analyzer_module, "engine", test_engine)
    yield
    test_engine.dispose()


def test_historical_clv_uses_entry_and_closing_snapshots(historical_clv_database):
    clv = CLVAnalyzer().get_historical_clv()

    home_clv = clv.loc[clv["Issue"] == "1"].iloc[0]
    assert home_clv["Cote Prise"] == pytest.approx(2.20)
    assert home_clv["Cote Clôture"] == pytest.approx(1.90)
    assert home_clv["CLV (%)"] == pytest.approx(15.78947368)
    assert home_clv["Anticipation (h)"] == pytest.approx(24.0)
    assert home_clv["Gain Fair Proba (pts)"] > 0


def test_excel_report_includes_clv_sheet_and_summary_rate(tmp_path, monkeypatch):
    clv_rows = pd.DataFrame(
        [
            ["2025-09-27", "Arsenal", "Chelsea", "1", 24.0, 2.20, 1.90, 15.79, 2.5],
            ["2025-09-27", "Arsenal", "Chelsea", "N", 24.0, 3.40, 3.60, -5.56, -1.0],
        ],
        columns=[
            "Date match",
            "Domicile",
            "Extérieur",
            "Issue",
            "Anticipation (h)",
            "Cote Prise",
            "Cote Clôture",
            "CLV (%)",
            "Gain Fair Proba (pts)",
        ],
    )
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
    odds_drift_data = pd.DataFrame(
        [
            {
                "fixture_id": 1,
                "competition": "Premier League",
                "kickoff_at": "2025-09-27 15:00:00",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "bookmaker": "Book A",
                "snapshot_count": 2,
                "open_odds_home": 2.20,
                "close_odds_home": 1.90,
                "drift_home_pct": -13.64,
                "open_odds_draw": 3.40,
                "close_odds_draw": 3.50,
                "drift_away_pct": 2.94,
                "open_odds_away": 3.60,
                "close_odds_away": 3.70,
            },
            {
                "fixture_id": 2,
                "competition": "Premier League",
                "kickoff_at": "2025-09-28 15:00:00",
                "home_team": "Liverpool",
                "away_team": "Everton",
                "bookmaker": "Book A",
                "snapshot_count": 2,
                "open_odds_home": 1.90,
                "close_odds_home": 2.20,
                "drift_home_pct": 15.79,
                "open_odds_draw": 3.50,
                "close_odds_draw": 3.40,
                "drift_away_pct": -2.78,
                "open_odds_away": 3.50,
                "close_odds_away": 3.40,
            },
            {
                "fixture_id": 3,
                "competition": "Premier League",
                "kickoff_at": "2025-09-29 15:00:00",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "bookmaker": "Book A",
                "snapshot_count": 1,
                "open_odds_home": 2.00,
                "close_odds_home": 2.00,
                "drift_home_pct": 0.0,
                "open_odds_draw": 3.40,
                "close_odds_draw": 3.40,
                "drift_away_pct": 0.0,
                "open_odds_away": 3.40,
                "close_odds_away": 3.40,
            },
        ]
    )
    monkeypatch.setattr(
        ExcelReportGenerator,
        "_load_dataframes",
        staticmethod(
            lambda: (
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(columns=drift_columns),
                clv_rows,
                odds_drift_data,
            )
        ),
    )
    output_path = tmp_path / "clv-report.xlsx"

    ExcelReportGenerator().generate_report(output_path)

    workbook = load_workbook(output_path)
    worksheet = workbook["CLV Backtest Analysis"]
    summary = workbook["Synthèse DQ"]
    drift_sheet = workbook["Analyse Odds Drift"]
    assert worksheet["H2"].value == pytest.approx(0.1579)
    assert worksheet["H2"].number_format == "+0.00%;-0.00%"
    assert worksheet["I2"].number_format == "+0.00%;-0.00%"
    assert worksheet["F2"].number_format == "0.00"
    assert worksheet["G2"].number_format == "0.00"
    assert summary["A8"].value == "Taux de battement CLV (%)"
    assert summary["B8"].value == pytest.approx(0.5)
    assert summary["B8"].number_format == "0.00%"
    assert len(worksheet.conditional_formatting) == 1
    assert drift_sheet["K4"].value == "En attente de snapshots (T0 unique)"
    assert drift_sheet["G2"].value == pytest.approx(-0.1364)
    assert len(drift_sheet.conditional_formatting) == 1


def test_clv_summary_reports_pending_for_t0_only_history():
    single_snapshot = pd.DataFrame(
        [
            {
                "fixture_id": 7,
                "competition": "Super League 2",
                "kickoff_at": "2026-09-27 15:00:00",
                "home_team": "AEL Larissa",
                "away_team": "PAOK B",
                "bookmaker": "Book A",
                "snapshot_count": 1,
                "open_odds_home": 2.0,
                "close_odds_home": 2.0,
                "drift_home_pct": 0.0,
                "open_odds_draw": 3.4,
                "close_odds_draw": 3.4,
                "drift_away_pct": 0.0,
                "open_odds_away": 3.4,
                "close_odds_away": 3.4,
            }
        ]
    )

    odds_drift_df, beat_rate = ExcelReportGenerator._prepare_odds_drift(single_snapshot)

    assert beat_rate == "En attente de snapshots (T0 unique)"
    assert odds_drift_df.iloc[0]["CLV Favorable (Oui/Non)"] == beat_rate

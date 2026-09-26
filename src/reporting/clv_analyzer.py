import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from src.database.connection import engine
from src.database.models import CleanOdds, Fixture, Team
from src.reporting.drift_analyzer import calculate_fair_probabilities


CLV_COLUMNS = [
    "Date match",
    "Domicile",
    "Extérieur",
    "Issue",
    "Anticipation (h)",
    "Cote Prise",
    "Cote Clôture",
    "CLV (%)",
    "Gain Fair Proba (pts)",
]


class CLVAnalyzer:
    def get_historical_clv(self, min_lead_hours: float = 1.0) -> pd.DataFrame:
        if min_lead_hours < 0:
            raise ValueError("min_lead_hours doit être positif ou nul.")

        closing_capture = (
            select(
                CleanOdds.fixture_id.label("fixture_id"),
                CleanOdds.bookmaker.label("bookmaker"),
                func.max(CleanOdds.captured_at).label("captured_at"),
            )
            .join(Fixture, Fixture.id == CleanOdds.fixture_id)
            .where(CleanOdds.captured_at <= Fixture.kickoff_at)
            .group_by(CleanOdds.fixture_id, CleanOdds.bookmaker)
            .subquery("closing_capture")
        )
        entry_odds = aliased(CleanOdds, name="entry_odds")
        closing_odds = aliased(CleanOdds, name="closing_odds")
        away_team = aliased(Team)

        query = (
            select(
                Fixture.kickoff_at.label("Date match"),
                Team.canonical_name.label("Domicile"),
                away_team.canonical_name.label("Extérieur"),
                Fixture.id.label("fixture_id"),
                entry_odds.bookmaker.label("bookmaker"),
                entry_odds.captured_at.label("entry_captured_at"),
                entry_odds.odds_home.label("entry_home"),
                entry_odds.odds_draw.label("entry_draw"),
                entry_odds.odds_away.label("entry_away"),
                closing_odds.captured_at.label("closing_captured_at"),
                closing_odds.odds_home.label("closing_home"),
                closing_odds.odds_draw.label("closing_draw"),
                closing_odds.odds_away.label("closing_away"),
            )
            .join(entry_odds, entry_odds.fixture_id == Fixture.id)
            .join(
                closing_capture,
                (closing_capture.c.fixture_id == Fixture.id)
                & (closing_capture.c.bookmaker == entry_odds.bookmaker),
            )
            .join(
                closing_odds,
                (closing_odds.fixture_id == closing_capture.c.fixture_id)
                & (closing_odds.bookmaker == closing_capture.c.bookmaker)
                & (closing_odds.captured_at == closing_capture.c.captured_at),
            )
            .join(Team, Team.id == Fixture.home_team_id)
            .join(away_team, away_team.id == Fixture.away_team_id)
            .where(
                Fixture.kickoff_at <= func.now(),
                entry_odds.captured_at < closing_capture.c.captured_at,
                entry_odds.captured_at <= Fixture.kickoff_at,
            )
        )

        with engine.connect() as connection:
            snapshots = pd.read_sql(query, connection)

        if snapshots.empty:
            return pd.DataFrame(columns=CLV_COLUMNS)

        kickoff_at = pd.to_datetime(snapshots["Date match"])
        entry_captured_at = pd.to_datetime(snapshots["entry_captured_at"])
        snapshots["Anticipation (h)"] = (
            (kickoff_at - entry_captured_at).dt.total_seconds() / 3600
        )
        snapshots = snapshots.loc[
            snapshots["Anticipation (h)"] >= min_lead_hours
        ].copy()
        if snapshots.empty:
            return pd.DataFrame(columns=CLV_COLUMNS)

        rows = []
        for _, snapshot in snapshots.iterrows():
            entry_probabilities = calculate_fair_probabilities(
                snapshot["entry_home"],
                snapshot["entry_draw"],
                snapshot["entry_away"],
            )
            closing_probabilities = calculate_fair_probabilities(
                snapshot["closing_home"],
                snapshot["closing_draw"],
                snapshot["closing_away"],
            )
            entry_odds_by_outcome = (
                snapshot["entry_home"],
                snapshot["entry_draw"],
                snapshot["entry_away"],
            )
            closing_odds_by_outcome = (
                snapshot["closing_home"],
                snapshot["closing_draw"],
                snapshot["closing_away"],
            )

            for outcome_index, outcome in enumerate(("1", "N", "2")):
                entry_odd = float(entry_odds_by_outcome[outcome_index])
                closing_odd = float(closing_odds_by_outcome[outcome_index])
                rows.append(
                    {
                        "Date match": snapshot["Date match"],
                        "Domicile": snapshot["Domicile"],
                        "Extérieur": snapshot["Extérieur"],
                        "Issue": outcome,
                        "Anticipation (h)": snapshot["Anticipation (h)"],
                        "Cote Prise": entry_odd,
                        "Cote Clôture": closing_odd,
                        "CLV (%)": ((entry_odd - closing_odd) / closing_odd) * 100,
                        "Gain Fair Proba (pts)": (
                            closing_probabilities[outcome_index]
                            - entry_probabilities[outcome_index]
                        )
                        * 100,
                    }
                )

        result = pd.DataFrame(rows, columns=CLV_COLUMNS)
        return result.sort_values(
            ["Date match", "CLV (%)"],
            ascending=[False, False],
            kind="stable",
        ).reset_index(drop=True)

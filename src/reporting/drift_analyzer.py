import pandas as pd
from sqlalchemy import bindparam, func, select, union_all
from sqlalchemy.orm import aliased

from src.database.connection import engine
from src.database.models import CleanOdds, Fixture, Team


class MarketDriftAnalyzer:
    def get_significant_drifts(self, threshold_pct: float = 3.0) -> pd.DataFrame:
        away_team = aliased(Team)
        previous_home = func.lag(CleanOdds.odds_home).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        previous_draw = func.lag(CleanOdds.odds_draw).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        previous_away = func.lag(CleanOdds.odds_away).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        previous_capture = func.lag(CleanOdds.captured_at).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        snapshots = (
            select(
                Fixture.kickoff_at.label("Date match"),
                Team.canonical_name.label("Domicile"),
                away_team.canonical_name.label("Extérieur"),
                CleanOdds.fixture_id.label("fixture_id"),
                CleanOdds.bookmaker.label("bookmaker"),
                CleanOdds.captured_at.label("Date snapshot actuel"),
                previous_capture.label("Date snapshot précédent"),
                CleanOdds.odds_home.label("current_home"),
                previous_home.label("previous_home"),
                CleanOdds.odds_draw.label("current_draw"),
                previous_draw.label("previous_draw"),
                CleanOdds.odds_away.label("current_away"),
                previous_away.label("previous_away"),
            )
            .join(Fixture, Fixture.id == CleanOdds.fixture_id)
            .join(Team, Team.id == Fixture.home_team_id)
            .join(away_team, away_team.id == Fixture.away_team_id)
            .cte("snapshots")
        )

        market_rows = []
        for outcome, current_column, previous_column in (
            ("1", snapshots.c.current_home, snapshots.c.previous_home),
            ("N", snapshots.c.current_draw, snapshots.c.previous_draw),
            ("2", snapshots.c.current_away, snapshots.c.previous_away),
        ):
            drift_pct = (
                (current_column - previous_column) / previous_column * 100.0
            )
            market_rows.append(
                select(
                    snapshots.c["Date match"],
                    snapshots.c.Domicile,
                    snapshots.c["Extérieur"],
                    bindparam("outcome_{}".format(outcome), outcome).label("Issue"),
                    previous_column.label("Ancienne cote"),
                    current_column.label("Nouvelle cote"),
                    drift_pct.label("Variation (%)"),
                    snapshots.c["Date snapshot précédent"],
                    snapshots.c["Date snapshot actuel"],
                ).where(previous_column.is_not(None))
            )

        all_markets = union_all(*market_rows).subquery("all_market_drifts")
        query = (
            select(
                all_markets.c["Date match"],
                all_markets.c.Domicile,
                all_markets.c["Extérieur"],
                all_markets.c.Issue,
                all_markets.c["Ancienne cote"],
                all_markets.c["Nouvelle cote"],
                all_markets.c["Variation (%)"],
                all_markets.c["Date snapshot précédent"],
                all_markets.c["Date snapshot actuel"],
            )
            .where(func.abs(all_markets.c["Variation (%)"]) >= bindparam("threshold_pct"))
            .order_by(func.abs(all_markets.c["Variation (%)"]).desc())
        )

        with engine.connect() as connection:
            return pd.read_sql(query, connection, params={"threshold_pct": threshold_pct})
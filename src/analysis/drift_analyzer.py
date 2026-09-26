from typing import Optional, Union

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from src.database.models import CleanOdds, Fixture, Team


ODDS_COLUMNS = {
    "home": "odds_home",
    "draw": "odds_draw",
    "away": "odds_away",
}


class OddsDriftAnalyzer:
    def __init__(self, source: Union[Session, pd.DataFrame]) -> None:
        if isinstance(source, pd.DataFrame):
            self._snapshots = source.copy()
            self._session = None
        elif isinstance(source, Session):
            self._snapshots = None
            self._session = source
        else:
            raise TypeError("source doit être une Session SQLAlchemy ou un DataFrame pandas.")

    def get_opening_and_closing_odds(
        self,
        competition: Optional[str] = None,
    ) -> pd.DataFrame:
        snapshots = self._load_snapshots(competition)
        if snapshots.empty:
            return pd.DataFrame(columns=self._output_columns(snapshots))

        required_columns = {"fixture_id", "captured_at", *ODDS_COLUMNS.values()}
        missing_columns = required_columns - set(snapshots.columns)
        if missing_columns:
            raise ValueError(
                "Colonnes manquantes dans les snapshots: {}".format(
                    ", ".join(sorted(missing_columns))
                )
            )

        snapshots["captured_at"] = pd.to_datetime(snapshots["captured_at"], utc=True)
        group_columns = ["fixture_id"]
        if "bookmaker" in snapshots.columns:
            group_columns.append("bookmaker")
        snapshots = snapshots.sort_values("captured_at", kind="stable")

        rows = []
        for _, group in snapshots.groupby(group_columns, dropna=False, sort=False):
            opening = group.iloc[0]
            closing = group.iloc[-1]
            record = {
                column: closing[column]
                for column in (
                    "fixture_id",
                    "competition",
                    "kickoff_at",
                    "home_team",
                    "away_team",
                    "bookmaker",
                )
                if column in group.columns
            }
            record["snapshot_count"] = len(group.index)
            record["open_captured_at"] = opening["captured_at"]
            record["close_captured_at"] = closing["captured_at"]
            for outcome, odds_column in ODDS_COLUMNS.items():
                record["open_odds_{}".format(outcome)] = float(opening[odds_column])
                record["close_odds_{}".format(outcome)] = float(closing[odds_column])
            rows.append(record)

        return pd.DataFrame(rows, columns=self._output_columns(snapshots))

    @staticmethod
    def calculate_clv_metrics(df: pd.DataFrame) -> pd.DataFrame:
        metrics = df.copy()
        for outcome in ODDS_COLUMNS:
            opening = metrics["open_odds_{}".format(outcome)]
            closing = metrics["close_odds_{}".format(outcome)]
            metrics["drift_{}".format(outcome)] = closing - opening
            metrics["drift_{}_pct".format(outcome)] = (closing / opening - 1.0) * 100
            metrics["clv_beat_{}_pct".format(outcome)] = (opening / closing - 1.0) * 100

        metrics["open_overround_pct"] = sum(
            100.0 / metrics["open_odds_{}".format(outcome)]
            for outcome in ODDS_COLUMNS
        )
        metrics["current_overround_pct"] = sum(
            100.0 / metrics["close_odds_{}".format(outcome)]
            for outcome in ODDS_COLUMNS
        )
        metrics["overround_change_pct_points"] = (
            metrics["current_overround_pct"] - metrics["open_overround_pct"]
        )
        return metrics

    def detect_steaming_lines(self, threshold_pct: float = 5.0) -> pd.DataFrame:
        if threshold_pct < 0:
            raise ValueError("threshold_pct doit être positif ou nul.")

        metrics = self.calculate_clv_metrics(
            self.get_opening_and_closing_odds()
        )
        steaming_rows = []
        for outcome in ODDS_COLUMNS:
            drift_column = "drift_{}_pct".format(outcome)
            market_moves = metrics.loc[metrics[drift_column] < -threshold_pct].copy()
            if market_moves.empty:
                continue
            market_moves["steaming_issue"] = outcome.upper()
            market_moves["steaming_drift_pct"] = market_moves[drift_column]
            steaming_rows.append(market_moves)

        if not steaming_rows:
            return metrics.iloc[0:0].assign(
                steaming_issue=pd.Series(dtype="object"),
                steaming_drift_pct=pd.Series(dtype="float64"),
            )
        return pd.concat(steaming_rows, ignore_index=True).sort_values(
            "steaming_drift_pct",
            ascending=True,
            kind="stable",
        ).reset_index(drop=True)

    def _load_snapshots(self, competition: Optional[str]) -> pd.DataFrame:
        if self._snapshots is not None:
            snapshots = self._snapshots.copy()
            if competition is not None and "competition" in snapshots.columns:
                snapshots = snapshots.loc[snapshots["competition"] == competition]
            return snapshots

        away_team = aliased(Team)
        query = select(
            CleanOdds.fixture_id,
            CleanOdds.bookmaker,
            CleanOdds.captured_at,
            CleanOdds.odds_home,
            CleanOdds.odds_draw,
            CleanOdds.odds_away,
            Fixture.competition,
            Fixture.kickoff_at,
            Team.canonical_name.label("home_team"),
            away_team.canonical_name.label("away_team"),
        ).join(Fixture, Fixture.id == CleanOdds.fixture_id)
        query = query.join(Team, Team.id == Fixture.home_team_id).join(
            away_team,
            away_team.id == Fixture.away_team_id,
        )
        if competition is not None:
            query = query.where(Fixture.competition == competition)
        return pd.DataFrame(self._session.execute(query).mappings().all())

    @staticmethod
    def _output_columns(snapshots: pd.DataFrame) -> list[str]:
        columns = [
            "fixture_id",
            "competition",
            "kickoff_at",
            "home_team",
            "away_team",
            "bookmaker",
            "snapshot_count",
        ]
        columns.extend(["open_captured_at", "close_captured_at"])
        for outcome in ODDS_COLUMNS:
            columns.extend(
                ["open_odds_{}".format(outcome), "close_odds_{}".format(outcome)]
            )
        return columns

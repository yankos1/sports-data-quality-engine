from math import isfinite

import pandas as pd
from sqlalchemy import bindparam, func, select, union_all
from sqlalchemy.orm import aliased

from src.database.connection import engine
from src.database.models import CleanOdds, Fixture, Team


def calculate_fair_probabilities(
    odds_home: float,
    odds_draw: float,
    odds_away: float,
) -> tuple[float, float, float]:
    odds = (odds_home, odds_draw, odds_away)
    if any(not isfinite(odd) or odd <= 0 for odd in odds):
        raise ValueError("Les cotes doivent être des nombres finis strictement positifs.")

    raw_probabilities = tuple(1.0 / odd for odd in odds)
    total_probability = sum(raw_probabilities)
    return tuple(probability / total_probability for probability in raw_probabilities)


def calculate_fair_odds(
    odds_home: float,
    odds_draw: float,
    odds_away: float,
) -> tuple[float, float, float]:
    return tuple(
        1.0 / probability
        for probability in calculate_fair_probabilities(odds_home, odds_draw, odds_away)
    )


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
        second_previous_home = func.lag(CleanOdds.odds_home, 2).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        second_previous_draw = func.lag(CleanOdds.odds_draw, 2).over(
            partition_by=(CleanOdds.fixture_id, CleanOdds.bookmaker),
            order_by=CleanOdds.captured_at.asc(),
        )
        second_previous_away = func.lag(CleanOdds.odds_away, 2).over(
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
                second_previous_home.label("second_previous_home"),
                second_previous_draw.label("second_previous_draw"),
                second_previous_away.label("second_previous_away"),
            )
            .join(Fixture, Fixture.id == CleanOdds.fixture_id)
            .join(Team, Team.id == Fixture.home_team_id)
            .join(away_team, away_team.id == Fixture.away_team_id)
            .cte("snapshots")
        )

        market_rows = []
        for outcome, current_column, previous_column, second_previous_column in (
            ("1", snapshots.c.current_home, snapshots.c.previous_home, snapshots.c.second_previous_home),
            ("N", snapshots.c.current_draw, snapshots.c.previous_draw, snapshots.c.second_previous_draw),
            ("2", snapshots.c.current_away, snapshots.c.previous_away, snapshots.c.second_previous_away),
        ):
            drift_pct = (
                (current_column - previous_column) / previous_column * 100.0
            )
            drift_1 = (
                (previous_column - second_previous_column) / second_previous_column
            )
            drift_2 = (current_column - previous_column) / previous_column
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
                    snapshots.c.previous_home.label("previous_home"),
                    snapshots.c.previous_draw.label("previous_draw"),
                    snapshots.c.previous_away.label("previous_away"),
                    snapshots.c.current_home.label("current_home"),
                    snapshots.c.current_draw.label("current_draw"),
                    snapshots.c.current_away.label("current_away"),
                    drift_1.label("drift_1"),
                    drift_2.label("drift_2"),
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
                all_markets.c.previous_home,
                all_markets.c.previous_draw,
                all_markets.c.previous_away,
                all_markets.c.current_home,
                all_markets.c.current_draw,
                all_markets.c.current_away,
                all_markets.c.drift_1,
                all_markets.c.drift_2,
            )
            .where(func.abs(all_markets.c["Variation (%)"]) >= bindparam("threshold_pct"))
            .order_by(func.abs(all_markets.c["Variation (%)"]).desc())
        )

        with engine.connect() as connection:
            drifts = pd.read_sql(query, connection, params={"threshold_pct": threshold_pct})

        output_columns = [
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
        if drifts.empty:
            return pd.DataFrame(columns=output_columns)

        outcome_indexes = {"1": 0, "N": 1, "2": 2}
        old_probabilities = []
        new_probabilities = []
        probability_gains = []
        current_fair_odds = []
        market_signals = []
        reversal_intensities = []
        for row in drifts.itertuples(index=False, name=None):
            row_values = dict(zip(drifts.columns, row))
            old_fair_probabilities = calculate_fair_probabilities(
                row_values["previous_home"],
                row_values["previous_draw"],
                row_values["previous_away"],
            )
            new_fair_probabilities = calculate_fair_probabilities(
                row_values["current_home"],
                row_values["current_draw"],
                row_values["current_away"],
            )
            outcome_index = outcome_indexes[row_values["Issue"]]
            old_probability = old_fair_probabilities[outcome_index]
            new_probability = new_fair_probabilities[outcome_index]
            old_probabilities.append(old_probability)
            new_probabilities.append(new_probability)
            probability_gains.append(new_probability - old_probability)
            current_fair_odds.append(
                calculate_fair_odds(
                    row_values["current_home"],
                    row_values["current_draw"],
                    row_values["current_away"],
                )[outcome_index]
            )
            drift_1 = row_values["drift_1"]
            drift_2 = row_values["drift_2"]
            if (
                pd.notna(drift_1)
                and pd.notna(drift_2)
                and drift_1 * drift_2 < 0
                and abs(drift_1) >= 0.04
                and abs(drift_2) >= 0.04
            ):
                if drift_1 < 0 < drift_2:
                    market_signals.append("SHARP REVERSAL (BEARISH)")
                else:
                    market_signals.append("SHARP REVERSAL (BULLISH)")
                reversal_intensities.append(abs(drift_2 - drift_1) * 100.0)
            else:
                market_signals.append("TREND CONTINUATION")
                reversal_intensities.append(None)

        drifts["Ancienne proba (%)"] = old_probabilities
        drifts["Nouvelle proba (%)"] = new_probabilities
        drifts["Gain de proba (pts)"] = probability_gains
        drifts["Cote Fair actuelle"] = current_fair_odds
        drifts["Signal Marché"] = market_signals
        drifts["Reversal Intensity (%)"] = reversal_intensities
        return drifts[output_columns]
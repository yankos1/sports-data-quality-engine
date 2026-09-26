from typing import Optional
from statistics import median


MIN_LEAGUE_OBSERVATIONS = 10
FALLBACK_OVERROUND_MIN_PCT = 100.0
FALLBACK_OVERROUND_MAX_PCT = 125.0
CROSS_BOOKMAKER_DEVIATION_THRESHOLD = 0.25


class AnomalyDetector:
    def evaluate_overround(
        self,
        overround_pct: float,
        league_history: Optional[list[float]] = None,
    ) -> list[dict]:
        history = league_history or []
        if len(history) < MIN_LEAGUE_OBSERVATIONS:
            if FALLBACK_OVERROUND_MIN_PCT <= overround_pct <= FALLBACK_OVERROUND_MAX_PCT:
                return []
            return [
                {
                    "rule_code": "DQ_WARN_STATISTICAL_OUTLIER",
                    "message": (
                        f"Overround hors garde métier ({overround_pct:.2f}%, "
                        f"historique ligue insuffisant: {len(history)} observations)"
                    ),
                    "severity": "WARNING",
                }
            ]

        league_mean = sum(history) / len(history)
        variance = sum((value - league_mean) ** 2 for value in history) / len(history)
        league_stddev = variance**0.5
        z_score = (
            abs(overround_pct - league_mean) / league_stddev
            if league_stddev > 0
            else (0.0 if overround_pct == league_mean else float("inf"))
        )
        if z_score > 3.0:
            return [
                {
                    "rule_code": "DQ_WARN_STATISTICAL_OUTLIER",
                    "message": (
                        f"Overround atypique ({overround_pct:.2f}%, "
                        f"Z-Score={z_score:.2f}, ligue μ={league_mean:.2f}%, "
                        f"σ={league_stddev:.2f}%)"
                    ),
                    "severity": "WARNING",
                }
            ]
        return []

    def evaluate_cross_bookmaker(
        self,
        odds_series: list[float],
        current_odd: float,
    ) -> list[dict]:
        if len(odds_series) < 3:
            return []

        median_odd = median(odds_series)
        if median_odd <= 0:
            return []

        relative_deviation = abs(current_odd - median_odd) / median_odd
        if relative_deviation <= CROSS_BOOKMAKER_DEVIATION_THRESHOLD:
            return []

        return [
            {
                "rule_code": "DQ_WARN_CROSS_BOOKMAKER_OUTLIER",
                "message": (
                    f"Cote atypique ({current_odd:.2f} vs médiane "
                    f"{median_odd:.2f}, écart relatif={relative_deviation:.1%})"
                ),
                "severity": "WARNING",
            }
        ]
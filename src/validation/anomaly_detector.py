from statistics import median


OVERROUND_MEAN_PCT = 106.2
OVERROUND_STDDEV_PCT = 1.1
OVERROUND_MIN_PCT = 102.5
OVERROUND_MAX_PCT = 110.0
CROSS_BOOKMAKER_DEVIATION_THRESHOLD = 0.25


class AnomalyDetector:
    def evaluate_overround(self, overround_pct: float) -> list[dict]:
        z_score = abs(overround_pct - OVERROUND_MEAN_PCT) / OVERROUND_STDDEV_PCT
        if z_score > 3.0 or not OVERROUND_MIN_PCT <= overround_pct <= OVERROUND_MAX_PCT:
            return [
                {
                    "rule_code": "DQ_WARN_STATISTICAL_OUTLIER",
                    "message": (
                        f"Overround atypique ({overround_pct:.2f}%, "
                        f"Z-Score={z_score:.2f})"
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
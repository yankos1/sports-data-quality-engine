from src.validation.anomaly_detector import AnomalyDetector


def test_nominal_overround_has_no_alert():
    detector = AnomalyDetector()

    assert detector.evaluate_overround(106.0) == []


def test_statistical_overround_outlier_has_warning():
    detector = AnomalyDetector()
    league_history = [105.9, 106.5, 106.1, 106.3, 106.0, 106.4, 106.2, 106.1, 106.3, 106.2]

    alerts = detector.evaluate_overround(112.5, league_history)

    assert len(alerts) == 1
    assert alerts[0]["rule_code"] == "DQ_WARN_STATISTICAL_OUTLIER"
    assert alerts[0]["severity"] == "WARNING"
    assert "112.50%" in alerts[0]["message"]
    assert "Z-Score=" in alerts[0]["message"]


def test_higher_secondary_league_profile_does_not_raise_false_warning():
    detector = AnomalyDetector()
    greek_league_history = [110.4, 110.8, 110.6, 110.9, 110.5, 110.7, 110.6, 110.8, 110.5, 110.7]

    assert detector.evaluate_overround(110.8, greek_league_history) == []


def test_small_league_history_uses_business_guard_band():
    detector = AnomalyDetector()

    assert detector.evaluate_overround(111.0, [106.0, 107.0]) == []
    assert detector.evaluate_overround(126.0, [106.0, 107.0])[0]["rule_code"] == (
        "DQ_WARN_STATISTICAL_OUTLIER"
    )


def test_cross_bookmaker_outlier_compares_to_median():
    detector = AnomalyDetector()

    alerts = detector.evaluate_cross_bookmaker([2.10, 2.15, 2.05], 3.40)

    assert len(alerts) == 1
    assert alerts[0]["rule_code"] == "DQ_WARN_CROSS_BOOKMAKER_OUTLIER"
    assert alerts[0]["severity"] == "WARNING"
    assert "médiane 2.10" in alerts[0]["message"]
    assert "écart relatif=61.9%" in alerts[0]["message"]
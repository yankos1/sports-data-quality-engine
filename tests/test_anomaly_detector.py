from src.validation.anomaly_detector import AnomalyDetector


def test_nominal_overround_has_no_alert():
    detector = AnomalyDetector()

    assert detector.evaluate_overround(106.0) == []


def test_statistical_overround_outlier_has_warning():
    detector = AnomalyDetector()

    alerts = detector.evaluate_overround(112.5)

    assert len(alerts) == 1
    assert alerts[0]["rule_code"] == "DQ_WARN_STATISTICAL_OUTLIER"
    assert alerts[0]["severity"] == "WARNING"
    assert "112.50%" in alerts[0]["message"]
    assert "Z-Score=5.73" in alerts[0]["message"]


def test_cross_bookmaker_outlier_compares_to_median():
    detector = AnomalyDetector()

    alerts = detector.evaluate_cross_bookmaker([2.10, 2.15, 2.05], 3.40)

    assert len(alerts) == 1
    assert alerts[0]["rule_code"] == "DQ_WARN_CROSS_BOOKMAKER_OUTLIER"
    assert alerts[0]["severity"] == "WARNING"
    assert "médiane 2.10" in alerts[0]["message"]
    assert "écart relatif=61.9%" in alerts[0]["message"]
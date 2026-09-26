import argparse
import logging
from time import perf_counter
from typing import Optional, Sequence

from src.ingestion.golden_source import load_canonical_teams
from src.reporting.excel_exporter import ExcelReportGenerator
from src.scrapers.live_odds_scraper import LiveOddsScraper, SUPPORTED_LEAGUES
from src.validation.dq_engine import DQEngine


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exécute le pipeline de qualité des données de cotes sportives."
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Réutilise data/raw/raw_odds_latest.json sans lancer le scraper.",
    )
    parser.add_argument(
        "--skip-golden",
        action="store_true",
        help="Ne télécharge pas les CSV de référence et ne recharge pas les équipes.",
    )
    parser.add_argument(
        "--league",
        choices=tuple(SUPPORTED_LEAGUES),
        default="super-league-2",
        help="Ligue à scraper (défaut : super-league-2).",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=4,
        help="Horizon de collecte en jours (défaut : 4).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    started_at = perf_counter()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    try:
        if args.skip_golden:
            LOGGER.info("Étape Golden Source ignorée (--skip-golden).")
        else:
            LOGGER.info(
                "Étape 1/4 : chargement du Golden Source pour %s.",
                args.league,
            )
            added_count = load_canonical_teams(args.league)
            LOGGER.info(
                "Golden Source terminé : %s clubs ajoutés.",
                added_count,
            )

        if args.skip_scrape:
            LOGGER.info(
                "Étape scraping ignorée (--skip-scrape) : réutilisation de "
                "data/raw/raw_odds_latest.json."
            )
        else:
            LOGGER.info("Étape 2/4 : scraping des rencontres et des cotes à venir.")
            fixtures = LiveOddsScraper(
                league=args.league,
                max_days_ahead=args.days_ahead,
            ).scrape_upcoming_fixtures()
            LOGGER.info("Scraping terminé : %s rencontres extraites.", len(fixtures))

        LOGGER.info("Étape 3/4 : validation DQ et persistance en base.")
        metrics = DQEngine().run_pipeline()
        LOGGER.info(
            "Validation terminée : %s cotes persistées, %s rejets.",
            metrics["odds_persisted"],
            metrics["rejected_total"],
        )

        LOGGER.info("Étape 4/4 : génération du rapport Excel.")
        report_path = ExcelReportGenerator().generate_report()
        LOGGER.info("Rapport disponible : %s", report_path)
        return 0
    except Exception:
        LOGGER.exception("Le pipeline a échoué.")
        return 1
    finally:
        elapsed_seconds = perf_counter() - started_at
        LOGGER.info("Temps d'exécution total : %.2f secondes.", elapsed_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
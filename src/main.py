import argparse
import logging
from time import perf_counter
from typing import Optional, Sequence

from src.reporting.excel_exporter import ExcelReportGenerator
from src.scrapers.golden_source_loader import load_teams
from src.scrapers.live_odds_scraper import LiveOddsScraper
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    started_at = perf_counter()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    try:
        if args.skip_golden:
            LOGGER.info("Étape Golden Source ignorée (--skip-golden).")
        else:
            LOGGER.info("Étape 1/4 : chargement du Golden Source des équipes.")
            added_count, total_count = load_teams()
            LOGGER.info(
                "Golden Source terminé : %s clubs ajoutés, %s au total.",
                added_count,
                total_count,
            )

        if args.skip_scrape:
            LOGGER.info(
                "Étape scraping ignorée (--skip-scrape) : réutilisation de "
                "data/raw/raw_odds_latest.json."
            )
        else:
            LOGGER.info("Étape 2/4 : scraping des rencontres et des cotes à venir.")
            fixtures = LiveOddsScraper().scrape_upcoming_fixtures()
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
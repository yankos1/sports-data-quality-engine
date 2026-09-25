import logging
import sys
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import func, select

from src.database.connection import SessionLocal
from src.database.models import Team


LOGGER = logging.getLogger(__name__)
SEASON_CODES = ("2324", "2425", "2526", "2627")
DIVISION = "E0"
URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
CSV_PATH = REFERENCE_DIR / "e0_master.csv"


def download_source_csv() -> Path:
    """Download configured Premier League seasons and build a combined master CSV."""
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    season_frames = []

    for season in SEASON_CODES:
        source_url = URL_TEMPLATE.format(season=season, division=DIVISION)
        request = Request(
            source_url,
            headers={"User-Agent": "sports-data-quality-engine/1.0"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                csv_bytes = response.read()
            season_frame = pd.read_csv(BytesIO(csv_bytes))
            season_path = REFERENCE_DIR / "e0_{}.csv".format(season)
            season_path.write_bytes(csv_bytes)
            season_frame["Season"] = season
            season_frames.append(season_frame)
            LOGGER.info("Saison %s téléchargée : %s", season, season_path)
        except Exception as err:
            LOGGER.warning("Saison %s ignorée: %s", season, err)

    if not season_frames:
        raise RuntimeError("Aucun CSV de saison n'a pu être téléchargé ou lu.")

    combined = pd.concat(season_frames, ignore_index=True, sort=False)
    combined.to_csv(CSV_PATH, index=False)
    LOGGER.info("Golden Source multi-saisons sauvegardé : %s", CSV_PATH)
    return CSV_PATH


def load_teams() -> tuple[int, int]:
    """Download the source data and insert any clubs missing from the teams table."""
    csv_path = download_source_csv()
    return _load_teams_from_path(csv_path)


def _load_teams_from_path(csv_path: Path) -> tuple[int, int]:
    matches = pd.read_csv(csv_path)
    clubs = sorted(
        str(name).strip()
        for name in pd.concat([matches["HomeTeam"], matches["AwayTeam"]])
        .dropna()
        .unique()
        if str(name).strip()
    )

    added_clubs = []
    with SessionLocal() as session:
        try:
            existing_clubs = set(session.scalars(select(Team.canonical_name)).all())
            for club_name in clubs:
                if club_name not in existing_clubs:
                    session.add(Team(canonical_name=club_name))
                    added_clubs.append(club_name)

            session.flush()
            total_count = session.scalar(select(func.count()).select_from(Team)) or 0
            session.commit()
        except Exception:
            session.rollback()
            LOGGER.exception("Échec de l'ingestion des équipes Premier League.")
            raise

    LOGGER.info("Nouveaux clubs ajoutés : %s", added_clubs)
    LOGGER.info("Nombre total de clubs en base : %s", total_count)
    return len(added_clubs), total_count


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        load_teams()
        LOGGER.info("CSV source sauvegardé dans %s", CSV_PATH)
        return 0
    except Exception:
        LOGGER.exception("Échec du chargement de la source officielle.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
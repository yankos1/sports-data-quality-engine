import csv
import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.connection import SessionLocal
from src.database.models import Team, TeamAlias
from src.scrapers.golden_source_loader import load_teams


LOGGER = logging.getLogger(__name__)
CSV_LEAGUES = {"premier-league"}
FUZZY_ALIAS_THRESHOLD = 80.0
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"


def load_canonical_teams(
    league_name: str,
    session_factory: Optional[Callable[[], Session]] = None,
    seed_path: Optional[Path] = None,
) -> int:
    local_seed = _find_local_seed(league_name, seed_path)
    if local_seed is None:
        if league_name in CSV_LEAGUES:
            added_count, _ = load_teams()
            return added_count
        LOGGER.warning("Aucune source Golden Source configurée pour %r.", league_name)
        return 0

    team_names = _read_team_seed(local_seed)
    factory = session_factory or SessionLocal
    inserted_count = 0
    with factory() as session:
        try:
            existing_teams = dict(
                session.execute(select(Team.canonical_name, Team.id)).all()
            )
            existing_aliases = set(
                session.scalars(select(TeamAlias.normalized_alias)).all()
            )
            normalized_canonical_names = {
                _normalize_name(name) for name in existing_teams
            }

            for team_name in team_names:
                normalized_name = _normalize_name(team_name)
                if (
                    team_name in existing_teams
                    or normalized_name in normalized_canonical_names
                    or normalized_name in existing_aliases
                ):
                    continue

                fuzzy_match = (
                    process.extractOne(
                        team_name,
                        list(existing_teams),
                        scorer=fuzz.token_sort_ratio,
                    )
                    if existing_teams
                    else None
                )
                if fuzzy_match is not None and fuzzy_match[1] >= FUZZY_ALIAS_THRESHOLD:
                    canonical_name = fuzzy_match[0]
                    session.add(
                        TeamAlias(
                            team_id=existing_teams[canonical_name],
                            alias=team_name,
                            normalized_alias=normalized_name,
                        )
                    )
                    existing_aliases.add(normalized_name)
                    continue

                team = Team(canonical_name=team_name)
                session.add(team)
                session.flush()
                existing_teams[team_name] = team.id
                normalized_canonical_names.add(normalized_name)
                inserted_count += 1

            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            LOGGER.exception("Échec du chargement du seed Golden Source %s.", league_name)
            raise

    LOGGER.info(
        "%s clubs ajoutés depuis le seed local pour %s.",
        inserted_count,
        league_name,
    )
    return inserted_count


def sync_golden_source(league_name: str) -> int:
    """Backward-compatible name for the league-aware canonical team loader."""
    return load_canonical_teams(league_name)


def _find_local_seed(league_name: str, seed_path: Optional[Path]) -> Optional[Path]:
    if seed_path is not None:
        return Path(seed_path)

    league_keys = (league_name, league_name.replace("-", "_"))
    for league_key in dict.fromkeys(league_keys):
        for suffix in ("json", "csv"):
            candidate = REFERENCE_DIR / "{}_teams.{}".format(league_key, suffix)
            if candidate.is_file():
                return candidate
    return None


def _read_team_seed(seed_path: Path) -> list[str]:
    if seed_path.suffix.casefold() == ".json":
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("teams")
        if not isinstance(payload, list):
            raise ValueError("Le seed JSON doit contenir une liste d'équipes.")
        raw_names = payload
    elif seed_path.suffix.casefold() == ".csv":
        with seed_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            rows = list(csv.reader(csv_file))
        if not rows:
            return []

        name_headers = {
            "canonical_name",
            "team",
            "team_name",
            "name",
            "hometeam",
            "awayteam",
        }
        headers = [cell.strip().casefold() for cell in rows[0]]
        name_columns = [index for index, header in enumerate(headers) if header in name_headers]
        if name_columns:
            raw_names = [
                row[index]
                for row in rows[1:]
                for index in name_columns
                if index < len(row)
            ]
        else:
            raw_names = [row[0] for row in rows if row]
    else:
        raise ValueError("Format de seed non pris en charge: {}".format(seed_path.suffix))

    if any(not isinstance(name, str) or not name.strip() for name in raw_names):
        raise ValueError("Le seed doit contenir uniquement des noms d'équipes non vides.")
    return sorted({name.strip() for name in raw_names})


def _normalize_name(name: str) -> str:
    return re.sub(r"[\W_]+", "", name.casefold(), flags=re.UNICODE)

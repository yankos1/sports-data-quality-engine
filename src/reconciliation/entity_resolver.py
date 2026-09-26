import logging
import re
from typing import Optional

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.connection import SessionLocal
from src.database.models import Team, TeamAlias


LOGGER = logging.getLogger(__name__)
COMMON_ALIASES = {
    "man utd": "Man United",
    "manchester utd": "Man United",
    "man city": "Man City",
    "manchester city": "Man City",
    "wolves": "Wolves",
    "wolverhampton": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "spurs": "Tottenham",
    "tottenham hotspur": "Tottenham",
    "nottingham forest": "Nott'm Forest",
    "notts forest": "Nott'm Forest",
    "nottingham": "Nott'm Forest",
    "leeds": "Leeds",
    "leeds united": "Leeds",
    "ipswich": "Ipswich",
    "ipswich town": "Ipswich",
    "sunderland": "Sunderland",
    "coventry": "Coventry",
    "hull": "Hull",
    "hull city": "Hull",
    "leicester": "Leicester",
    "leicester city": "Leicester",
    "southampton": "Southampton",
    "sheffield utd": "Sheffield United",
    "newcastle utd": "Newcastle",
    "brighton & hove albion": "Brighton",
    "west ham united": "West Ham",
}
AUTO_MATCH_THRESHOLD = 85.0
LOW_CONFIDENCE_THRESHOLD = 65.0


class EntityResolver:
    def __init__(
        self,
        canonical_names: Optional[list[str]] = None,
        session: Optional[Session] = None,
    ) -> None:
        self._session = session
        if session is None:
            with SessionLocal() as database_session:
                if canonical_names is None:
                    canonical_names = list(
                        database_session.scalars(select(Team.canonical_name)).all()
                    )
                persisted_aliases = self._load_persisted_aliases(database_session)
        else:
            if canonical_names is None:
                canonical_names = list(session.scalars(select(Team.canonical_name)).all())
            persisted_aliases = self._load_persisted_aliases(session)

        self.canonical_names = sorted(
            {name.strip() for name in canonical_names if name and name.strip()}
        )
        self._normalized_names = {
            self._normalize(name): name for name in self.canonical_names
        }
        self._normalized_aliases = {
            self._normalize(alias): canonical_name
            for alias, canonical_name in COMMON_ALIASES.items()
            if canonical_name in self.canonical_names
        }
        for normalized_alias, canonical_name in persisted_aliases:
            if (
                canonical_name in self.canonical_names
                and normalized_alias not in self._normalized_aliases
            ):
                self._normalized_aliases[normalized_alias] = canonical_name
        self._cache: dict[str, dict] = {}

    @staticmethod
    def _load_persisted_aliases(session: Session) -> list[tuple[str, str]]:
        return list(
            session.execute(
                select(TeamAlias.normalized_alias, Team.canonical_name).join(
                    Team, Team.id == TeamAlias.team_id
                )
            ).all()
        )

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"[\W_]+", "", name.casefold(), flags=re.UNICODE)

    def resolve(self, raw_name: str) -> dict:
        raw_name = raw_name.strip() if isinstance(raw_name, str) else ""
        normalized_name = self._normalize(raw_name)
        if normalized_name in self._cache:
            return self._cache[normalized_name].copy()

        exact_match = self._normalized_names.get(normalized_name)
        if exact_match is not None:
            result = self._result(raw_name, exact_match, "exact", 1.0)
            self._cache[normalized_name] = result
            return result.copy()

        alias_match = self._normalized_aliases.get(normalized_name)
        if alias_match in self.canonical_names:
            result = self._result(raw_name, alias_match, "alias", 1.0)
            self._cache[normalized_name] = result
            return result.copy()

        fuzzy_match = process.extractOne(
            raw_name,
            self.canonical_names,
            scorer=fuzz.token_sort_ratio,
        ) if raw_name and self.canonical_names else None

        if fuzzy_match is not None:
            candidate, score, _ = fuzzy_match
            if score >= AUTO_MATCH_THRESHOLD:
                result = self._result(
                    raw_name,
                    candidate,
                    "fuzzy",
                    round(score / 100.0, 3),
                )
                self._persist_alias(raw_name, candidate)
                self._normalized_aliases[normalized_name] = candidate
                self._cache[normalized_name] = result
                return result.copy()

            if score >= LOW_CONFIDENCE_THRESHOLD:
                LOGGER.warning(
                    "Résolution fuzzy à confiance intermédiaire pour %r: %s (%0.1f%%).",
                    raw_name,
                    candidate,
                    score,
                )
                result = self._result(
                    raw_name,
                    candidate,
                    "fuzzy",
                    round(score / 100.0, 3),
                )
                self._cache[normalized_name] = result
                return result.copy()

        result = self._result(raw_name, None, "unresolved", 0.0)
        self._cache[normalized_name] = result
        return result.copy()

    def _persist_alias(self, alias: str, canonical_name: str) -> None:
        if not alias or len(alias) > 255:
            return

        if self._session is not None:
            self._save_alias(self._session, alias, canonical_name)
            return

        with SessionLocal() as session:
            self._save_alias(session, alias, canonical_name)
            session.commit()

    def _save_alias(self, session: Session, alias: str, canonical_name: str) -> None:
        normalized_alias = self._normalize(alias)
        team_id = session.scalar(
            select(Team.id).where(Team.canonical_name == canonical_name)
        )
        if team_id is None:
            LOGGER.warning(
                "Alias fuzzy %r non persisté: équipe canonique %r absente de la base.",
                alias,
                canonical_name,
            )
            return

        existing_alias = session.scalar(
            select(TeamAlias).where(TeamAlias.normalized_alias == normalized_alias)
        )
        if existing_alias is not None:
            if existing_alias.team_id != team_id:
                LOGGER.warning(
                    "Alias %r déjà associé à une autre équipe; association conservée.",
                    alias,
                )
            return

        try:
            with session.begin_nested():
                session.add(
                    TeamAlias(
                        team_id=team_id,
                        alias=alias,
                        normalized_alias=normalized_alias,
                    )
                )
                session.flush()
        except IntegrityError:
            LOGGER.info("Alias %r déjà persisté par un autre traitement.", alias)

    @staticmethod
    def _result(
        raw_name: str,
        canonical_name: Optional[str],
        method: str,
        confidence: float,
    ) -> dict:
        return {
            "raw_name": raw_name,
            "canonical_name": canonical_name,
            "method": method,
            "confidence": confidence,
        }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    resolver = EntityResolver()
    test_names = [
        "Arsenal",
        "Man Utd",
        "Wolverhampton",
        "Spurs",
        "Bournemouth",
        "Fake Madrid CF",
    ]
    results = [resolver.resolve(name) for name in test_names]
    for result in results:
        print(result)

    if any(result["canonical_name"] is None for result in results[:5]):
        raise AssertionError("Au moins un club connu n'a pas été résolu.")
    if results[5]["canonical_name"] is not None:
        raise AssertionError("Fake Madrid CF ne devrait pas être résolu.")
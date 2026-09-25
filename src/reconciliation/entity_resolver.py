import json
import logging
import re
from typing import Optional

from openai import OpenAI
from rapidfuzz import fuzz, process
from sqlalchemy import select

from src.config import OPENAI_API_KEY
from src.database.connection import SessionLocal
from src.database.models import Team


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
FUZZY_THRESHOLD = 78.0
LLM_CONFIDENCE_THRESHOLD = 0.80


class EntityResolver:
    def __init__(self, canonical_names: Optional[list[str]] = None) -> None:
        if canonical_names is None:
            with SessionLocal() as session:
                canonical_names = list(session.scalars(select(Team.canonical_name)).all())

        self.canonical_names = sorted(
            {name.strip() for name in canonical_names if name and name.strip()}
        )
        self._normalized_names = {
            self._normalize(name): name for name in self.canonical_names
        }
        self._normalized_aliases = {
            self._normalize(alias): canonical_name
            for alias, canonical_name in COMMON_ALIASES.items()
        }
        self._cache: dict[str, dict] = {}
        self._client: Optional[OpenAI] = None

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"[\W_]+", "", name.casefold(), flags=re.UNICODE)

    def resolve(self, raw_name: str) -> dict:
        raw_name = raw_name.strip() if isinstance(raw_name, str) else ""
        normalized_name = self._normalize(raw_name)
        if normalized_name in self._cache:
            return self._cache[normalized_name].copy()

        alias_match = self._normalized_aliases.get(normalized_name)
        if alias_match in self.canonical_names:
            result = self._result(raw_name, alias_match, "alias", 1.0)
            self._cache[normalized_name] = result
            return result.copy()

        exact_match = self._normalized_names.get(normalized_name)
        if exact_match is not None:
            result = self._result(raw_name, exact_match, "exact", 1.0)
            self._cache[normalized_name] = result
            return result.copy()

        fuzzy_match = process.extractOne(
            raw_name,
            self.canonical_names,
            scorer=fuzz.token_sort_ratio,
        ) if raw_name and self.canonical_names else None

        if fuzzy_match is not None:
            candidate, score, _ = fuzzy_match
            if score >= FUZZY_THRESHOLD:
                result = self._result(
                    raw_name,
                    candidate,
                    "fuzzy",
                    round(score / 100.0, 3),
                )
                self._cache[normalized_name] = result
                return result.copy()

        if fuzzy_match is not None and OPENAI_API_KEY:
            llm_result = self._resolve_with_llm(raw_name)
            if (
                llm_result is not None
                and llm_result["canonical_name"] in self.canonical_names
                and llm_result["confidence"] >= LLM_CONFIDENCE_THRESHOLD
            ):
                result = self._result(
                    raw_name,
                    llm_result["canonical_name"],
                    "llm",
                    llm_result["confidence"],
                )
                self._cache[normalized_name] = result
                return result.copy()

        result = self._result(raw_name, None, "unresolved", 0.0)
        self._cache[normalized_name] = result
        return result.copy()

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

    def _resolve_with_llm(self, raw_name: str) -> Optional[dict]:
        if self._client is None:
            self._client = OpenAI(api_key=OPENAI_API_KEY, timeout=15.0, max_retries=1)

        schema = {
            "type": "object",
            "properties": {
                "canonical_name": {
                    "type": ["string", "null"],
                    "enum": self.canonical_names + [None],
                },
                "confidence": {"type": "number"},
            },
            "required": ["canonical_name", "confidence"],
            "additionalProperties": False,
        }

        try:
            response = self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Resolve a football club alias to exactly one canonical name "
                            "from the supplied list. Do not guess. If no confident match "
                            "exists, return null with confidence 0. Return confidence "
                            "between 0 and 1."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "raw_name": raw_name,
                                "canonical_names": self.canonical_names,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "club_entity_resolution",
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
            content = response.choices[0].message.content
            if not content:
                return None

            result = json.loads(content)
            confidence = float(result["confidence"])
            if not 0.0 <= confidence <= 1.0:
                return None
            return {
                "canonical_name": result["canonical_name"],
                "confidence": confidence,
            }
        except Exception:
            LOGGER.exception("Échec du fallback OpenAI pour le nom %r.", raw_name)
            return None


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
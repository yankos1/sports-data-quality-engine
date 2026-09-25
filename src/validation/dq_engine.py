import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.connection import SessionLocal
from src.database.models import CleanOdds, DQException, Fixture, Team
from src.reconciliation.entity_resolver import EntityResolver


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_FILE_PATH = PROJECT_ROOT / "data" / "raw" / "raw_odds_latest.json"
ODDS_FIELDS = ("odds_home", "odds_draw", "odds_away")
DQ_RULE_CODES = (
    "DQ_ERR_UNRESOLVED_TEAM",
    "DQ_INFO_UNOPENED_MARKET",
    "DQ_ERR_INVALID_ODDS",
    "DQ_ERR_SUSPECT_PERMUTATION",
)


class DQEngine:
    def run_pipeline(self, raw_file_path: Optional[Path] = None) -> dict:
        input_path = raw_file_path or DEFAULT_RAW_FILE_PATH
        raw_payload = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(raw_payload, list):
            raw_fixtures = raw_payload
            captured_at = datetime.now(timezone.utc).replace(
                tzinfo=None, microsecond=0
            )
        elif isinstance(raw_payload, dict) and isinstance(raw_payload.get("fixtures"), list):
            raw_fixtures = raw_payload["fixtures"]
            captured_at = self._parse_datetime(raw_payload.get("scraped_at")) or datetime.now(
                timezone.utc
            ).replace(tzinfo=None, microsecond=0)
        else:
            raise ValueError("Le fichier brut doit contenir une liste 'fixtures'.")

        metrics = {
            "raw_fixtures": len(raw_fixtures),
            "fixtures_inserted": 0,
            "fixtures_reused": 0,
            "odds_persisted": 0,
            "rejected_total": 0,
            "rejected_by_rule": {rule_code: 0 for rule_code in DQ_RULE_CODES},
        }

        with SessionLocal() as session:
            try:
                team_rows = session.execute(
                    select(Team.id, Team.canonical_name)
                ).all()
                team_ids = {canonical_name: team_id for team_id, canonical_name in team_rows}
                resolver = EntityResolver(canonical_names=list(team_ids))
                existing_odds_keys = set(
                    session.execute(
                        select(
                            CleanOdds.fixture_id,
                            CleanOdds.bookmaker,
                            CleanOdds.captured_at,
                        )
                    ).all()
                )
                existing_rejections = session.execute(
                    select(
                        DQException.rule_code,
                        DQException.message,
                        DQException.source,
                        DQException.rejected_payload,
                    )
                ).all()
                processed_rejection_keys = {
                    self._rejection_key(rule_code, message, source, rejected_payload)
                    for rule_code, message, source, rejected_payload in existing_rejections
                }

                for row_number, fixture_data in enumerate(raw_fixtures, start=1):
                    if not isinstance(fixture_data, dict):
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_UNRESOLVED_TEAM",
                            "La rencontre brute n'est pas un objet valide.",
                            {"raw_value": fixture_data},
                            None,
                            processed_rejection_keys,
                        )
                        continue

                    try:
                        home_result = resolver.resolve(fixture_data.get("raw_home_team", ""))
                        away_result = resolver.resolve(fixture_data.get("raw_away_team", ""))
                        canonical_home = home_result["canonical_name"]
                        canonical_away = away_result["canonical_name"]
                    except Exception:
                        LOGGER.exception("Erreur de résolution à la ligne %s.", row_number)
                        raw_home = fixture_data.get("raw_home_team")
                        raw_away = fixture_data.get("raw_away_team")
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_UNRESOLVED_TEAM",
                            "Équipe mal référencée (domicile={}, extérieur={}).".format(
                                raw_home, raw_away
                            ),
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    if (
                        canonical_home is None
                        or canonical_away is None
                        or canonical_home == canonical_away
                        or canonical_home not in team_ids
                        or canonical_away not in team_ids
                    ):
                        raw_home = fixture_data.get("raw_home_team")
                        raw_away = fixture_data.get("raw_away_team")
                        message = "Équipe mal référencée (domicile={}, extérieur={}).".format(
                            raw_home, raw_away
                        )
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_UNRESOLVED_TEAM",
                            message,
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    raw_odds = [fixture_data.get(field) for field in ODDS_FIELDS]
                    if all(value is None for value in raw_odds):
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_INFO_UNOPENED_MARKET",
                            "Marché non ouvert : aucune cote publiée pour cette date.",
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    odds_values = [self._to_valid_odds(value) for value in raw_odds]
                    if any(value is None for value in odds_values):
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_INVALID_ODDS",
                            "Cote incomplète, corrompue ou non numérique "
                            "(1={}, N={}, 2={}).".format(*raw_odds),
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    odds_home, odds_draw, odds_away = odds_values
                    overround = (
                        Decimal("1") / odds_home
                        + Decimal("1") / odds_draw
                        + Decimal("1") / odds_away
                    )
                    if (
                        overround < Decimal("1.01")
                        or overround > Decimal("1.25")
                        or odds_draw < Decimal("1.80")
                    ):
                        reasons = []
                        if overround < Decimal("1.01") or overround > Decimal("1.25"):
                            reasons.append("overround hors tolérance: {:.4f}".format(overround))
                        if odds_draw < Decimal("1.80"):
                            reasons.append(
                                "Cote du nul suspecte (< 1.80), inversion probable de colonnes"
                            )
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_SUSPECT_PERMUTATION",
                            "Cotes permutées ou décalées suspectées ({})".format(
                                "; ".join(reasons)
                            ),
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    competition = fixture_data.get("competition")
                    kickoff_at = self._parse_datetime(fixture_data.get("kickoff_at"))
                    if not competition or kickoff_at is None:
                        self._record_rejection(
                            session,
                            metrics,
                            "DQ_ERR_UNRESOLVED_TEAM",
                            "Compétition ou date de coup d'envoi absente ou invalide.",
                            fixture_data,
                            fixture_data.get("bookmaker"),
                            processed_rejection_keys,
                        )
                        continue

                    home_team_id = team_ids[canonical_home]
                    away_team_id = team_ids[canonical_away]
                    fixture = session.scalar(
                        select(Fixture).where(
                            Fixture.competition == competition,
                            Fixture.kickoff_at == kickoff_at,
                            Fixture.home_team_id == home_team_id,
                            Fixture.away_team_id == away_team_id,
                        )
                    )
                    if fixture is None:
                        fixture = Fixture(
                            competition=competition,
                            kickoff_at=kickoff_at,
                            home_team_id=home_team_id,
                            away_team_id=away_team_id,
                        )
                        session.add(fixture)
                        session.flush()
                        metrics["fixtures_inserted"] += 1
                    else:
                        metrics["fixtures_reused"] += 1

                    bookmaker = fixture_data.get("bookmaker") or "Unknown"
                    odds_key = (fixture.id, bookmaker, captured_at)
                    if odds_key in existing_odds_keys:
                        continue

                    session.add(
                        CleanOdds(
                            fixture_id=fixture.id,
                            bookmaker=bookmaker,
                            odds_home=odds_home,
                            odds_draw=odds_draw,
                            odds_away=odds_away,
                            captured_at=captured_at,
                        )
                    )
                    existing_odds_keys.add(odds_key)
                    metrics["odds_persisted"] += 1

                metrics["rejected_total"] = sum(metrics["rejected_by_rule"].values())
                session.commit()
            except Exception:
                session.rollback()
                LOGGER.exception("Échec du pipeline de contrôle qualité; transaction annulée.")
                raise

        self._print_summary(metrics)
        return metrics

    def _record_rejection(
        self,
        session: Session,
        metrics: dict,
        rule_code: str,
        message: str,
        fixture_data: dict,
        source: Optional[str],
        processed_rejection_keys: set,
    ) -> None:
        rejection_key = self._rejection_key(rule_code, message, source, fixture_data)
        if rejection_key in processed_rejection_keys:
            return

        session.add(
            DQException(
                source=source,
                rule_code=rule_code,
                message=message,
                rejected_payload=fixture_data,
            )
        )
        processed_rejection_keys.add(rejection_key)
        metrics["rejected_by_rule"][rule_code] = (
            metrics["rejected_by_rule"].get(rule_code, 0) + 1
        )

    @staticmethod
    def _rejection_key(rule_code: str, message: str, source: Optional[str], payload: object) -> tuple:
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return rule_code, message, source, payload_json

    @staticmethod
    def _to_valid_odds(value: object) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            odds = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not odds.is_finite() or odds <= Decimal("1.00"):
            return None
        return odds

    @staticmethod
    def _parse_datetime(value: object) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed.replace(microsecond=0)

    @staticmethod
    def _print_summary(metrics: dict) -> None:
        print("Rencontres brutes analysées : {}".format(metrics["raw_fixtures"]))
        print(
            "Rencontres insérées dans fixtures : {} (réutilisées : {})".format(
                metrics["fixtures_inserted"], metrics["fixtures_reused"]
            )
        )
        print("Cotes persistées dans clean_odds : {}".format(metrics["odds_persisted"]))
        print("Rejets insérés dans dq_exceptions : {}".format(metrics["rejected_total"]))
        for rule_code in DQ_RULE_CODES:
            print("  {} : {}".format(rule_code, metrics["rejected_by_rule"].get(rule_code, 0)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    DQEngine().run_pipeline()
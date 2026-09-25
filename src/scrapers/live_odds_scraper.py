import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger(__name__)
SOURCE_URL = "https://www.betexplorer.com/football/england/premier-league/fixtures/"
BOOKMAKER_LABEL = "BetExplorer best odds"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "raw_odds_latest.json"

MATCH_SEPARATOR = re.compile(r"\s+[–—-]\s+")
KICKOFF_PATTERN = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\."
    r"(?:\s*(?P<year>\d{4}))?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)


class LiveOddsScraper:
    """Scrape upcoming Premier League fixtures and BetExplorer's best 1X2 odds."""

    def __init__(
        self,
        source_url: str = SOURCE_URL,
        output_path: Optional[Path] = None,
        timeout_ms: int = 15000,
    ) -> None:
        self.source_url = source_url
        self.output_path = output_path or DEFAULT_OUTPUT_PATH
        self.timeout_ms = timeout_ms

    def scrape_upcoming_fixtures(self) -> list[dict]:
        fixtures = []
        browser = None

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    locale="en-GB",
                    timezone_id="UTC",
                )
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                try:
                    page.goto(
                        self.source_url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    page.wait_for_selector("table tr", timeout=self.timeout_ms)
                except PlaywrightTimeoutError:
                    LOGGER.warning("Délai dépassé en attendant le tableau des rencontres.")

                rows = page.locator("table tr")
                last_kickoff = None

                for row_index in range(rows.count()):
                    row = rows.nth(row_index)
                    try:
                        cell_texts = [
                            text.strip()
                            for text in row.locator("td").all_text_contents()
                        ]
                        if not cell_texts:
                            continue

                        match = self._find_teams(cell_texts)
                        if match is None:
                            continue

                        kickoff = self._parse_kickoff(cell_texts)
                        if kickoff is not None:
                            last_kickoff = kickoff
                        elif last_kickoff is not None:
                            kickoff = last_kickoff

                        if kickoff is None:
                            LOGGER.debug("Horaire absent; ligne ignorée: %s", cell_texts)
                            continue

                        _, home_team, away_team = match
                        data_odd_values = row.locator("[data-odd]").evaluate_all(
                            "elements => elements.map(element => element.getAttribute('data-odd'))"
                        )
                        dedicated_odd_values = row.locator(
                            "td.table-main__detail-odds, td.odds"
                        ).all_text_contents()
                        odds = self._extract_odds(
                            cell_texts,
                            data_odd_values=data_odd_values,
                            dedicated_odd_values=dedicated_odd_values,
                        )

                        fixtures.append(
                            {
                                "competition": "Premier League",
                                "kickoff_at": kickoff,
                                "raw_home_team": home_team,
                                "raw_away_team": away_team,
                                "bookmaker": BOOKMAKER_LABEL,
                                "odds_home": odds[0],
                                "odds_draw": odds[1],
                                "odds_away": odds[2],
                            }
                        )
                    except (IndexError, ValueError, PlaywrightError) as error:
                        LOGGER.warning(
                            "Ligne %s ignorée après une erreur de lecture: %s",
                            row_index,
                            error,
                        )

                context.close()
        except PlaywrightError:
            LOGGER.exception("Impossible de récupérer les rencontres depuis %s", self.source_url)
        finally:
            if browser is not None:
                try:
                    browser.close()
                except PlaywrightError:
                    LOGGER.warning("Échec de fermeture du navigateur Chromium.")
            self._save_raw_payload(fixtures)

        return fixtures

    @staticmethod
    def _find_teams(cell_texts: list[str]):
        for index, text in enumerate(cell_texts):
            teams = MATCH_SEPARATOR.split(text, maxsplit=1)
            if len(teams) == 2 and teams[0].strip() and teams[1].strip():
                return index, teams[0].strip(), teams[1].strip()
        return None

    @staticmethod
    def _parse_kickoff(cell_texts: list[str]) -> Optional[datetime]:
        for text in cell_texts:
            match = KICKOFF_PATTERN.search(text)
            if match is None:
                continue

            now = datetime.now(timezone.utc)
            year = int(match.group("year") or now.year)
            kickoff = datetime(
                year,
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour")),
                int(match.group("minute")),
                tzinfo=timezone.utc,
            )
            if match.group("year") is None and kickoff < now - timedelta(days=1):
                kickoff = kickoff.replace(year=year + 1)
            return kickoff
        return None

    @staticmethod
    def _parse_odds(cell_texts: list[str], index: int) -> Optional[float]:
        if index >= len(cell_texts):
            return None
        return LiveOddsScraper._parse_odds_value(cell_texts[index])

    @staticmethod
    def _parse_odds_value(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        normalized_value = value.strip().replace(",", ".")
        try:
            odds = float(normalized_value)
        except ValueError:
            return None
        return odds if odds > 1.0 else None

    @classmethod
    def _extract_odds(
        cls,
        cell_texts: list[str],
        data_odd_values: Optional[list[Optional[str]]] = None,
        dedicated_odd_values: Optional[list[str]] = None,
    ) -> list[Optional[float]]:
        if data_odd_values and len(data_odd_values) >= 3:
            return [cls._parse_odds_value(value) for value in data_odd_values[-3:]]

        if dedicated_odd_values and len(dedicated_odd_values) >= 3:
            return [cls._parse_odds_value(value) for value in dedicated_odd_values[-3:]]

        numeric_values = [
            odds
            for odds in (cls._parse_odds_value(value) for value in cell_texts)
            if odds is not None
        ]
        if len(numeric_values) >= 3:
            return numeric_values[-3:]
        return [None, None, None]

    def _save_raw_payload(self, fixtures: list[dict]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_url": self.source_url,
            "fixtures": [
                {
                    **fixture,
                    "kickoff_at": fixture["kickoff_at"].isoformat(),
                }
                for fixture in fixtures
            ],
        }
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        LOGGER.info("Charge brute sauvegardée dans %s", self.output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    scraper = LiveOddsScraper()
    extracted_fixtures = scraper.scrape_upcoming_fixtures()
    print("Rencontres extraites : {}".format(len(extracted_fixtures)))
    for fixture in extracted_fixtures[:3]:
        print(fixture)
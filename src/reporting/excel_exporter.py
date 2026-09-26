import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import text

from src.database.connection import engine
from src.reporting.drift_analyzer import MarketDriftAnalyzer


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "reports" / "dq_report_latest.xlsx"

CLEAN_ODDS_QUERY = text(
    """
    SELECT
        f.kickoff_at AS `Date coup d'envoi UTC`,
        home_team.canonical_name AS `Équipe Domicile`,
        away_team.canonical_name AS `Équipe Extérieur`,
        clean_odds.bookmaker AS Bookmaker,
        clean_odds.odds_home AS `Cote 1`,
        clean_odds.odds_draw AS `Cote N`,
        clean_odds.odds_away AS `Cote 2`,
        (1.0 / clean_odds.odds_home
         + 1.0 / clean_odds.odds_draw
         + 1.0 / clean_odds.odds_away) * 100 AS `Overround (%)`,
        clean_odds.captured_at AS `Horodatage d'ingestion`
    FROM clean_odds
    JOIN (
        SELECT fixture_id, bookmaker, MAX(captured_at) AS captured_at
        FROM clean_odds
        GROUP BY fixture_id, bookmaker
    ) AS latest_odds
        ON latest_odds.fixture_id = clean_odds.fixture_id
        AND latest_odds.bookmaker = clean_odds.bookmaker
        AND latest_odds.captured_at = clean_odds.captured_at
    JOIN fixtures AS f ON f.id = clean_odds.fixture_id
    JOIN teams AS home_team ON home_team.id = f.home_team_id
    JOIN teams AS away_team ON away_team.id = f.away_team_id
    ORDER BY f.kickoff_at, home_team.canonical_name
    """
)

DQ_EXCEPTIONS_QUERY = text(
    """
    SELECT
        id AS ID,
        created_at AS Horodatage,
        rule_code AS `Code Règle`,
        message AS Message,
        source AS `Source/Bookmaker`,
        rejected_payload AS `Payload JSON brut`
    FROM dq_exceptions
    ORDER BY created_at DESC, id DESC
    """
)


class ExcelReportGenerator:
    def generate_report(self, output_path: Optional[Path] = None) -> Path:
        report_path = Path(output_path) if output_path is not None else DEFAULT_OUTPUT_PATH
        report_path = report_path.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)

        clean_odds_df, exceptions_df, drift_df = self._load_dataframes()
        total_clean_odds = len(clean_odds_df.index)
        total_exceptions = len(exceptions_df.index)
        total_analyzed = total_clean_odds + total_exceptions
        quality_score = (
            total_clean_odds / total_analyzed if total_analyzed else 0.0
        )
        rule_counts = (
            exceptions_df["Code Règle"].fillna("UNKNOWN").value_counts().sort_index()
            if total_exceptions
            else pd.Series(dtype="int64")
        )

        summary_metrics = pd.DataFrame(
            [
                ("Total cotes analysées", total_analyzed),
                ("Cotes conformes (clean_odds)", total_clean_odds),
                ("Anomalies (dq_exceptions)", total_exceptions),
                ("Data Quality Score", quality_score),
            ],
            columns=["Métrique", "Valeur"],
        )
        anomaly_counts = pd.DataFrame(
            [(rule_code, int(count)) for rule_code, count in rule_counts.items()],
            columns=["Code Règle", "Nombre d'anomalies"],
        )

        exceptions_df = exceptions_df.copy()
        if "Payload JSON brut" in exceptions_df.columns:
            exceptions_df["Payload JSON brut"] = exceptions_df[
                "Payload JSON brut"
            ].map(self._format_payload)
        if "Variation (%)" in drift_df.columns:
            drift_df = drift_df.copy()
            drift_df["Variation (%)"] = drift_df["Variation (%)"] / 100

        with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
            summary_metrics.to_excel(
                writer,
                sheet_name="Executive Summary",
                startrow=2,
                index=False,
            )
            anomaly_counts.to_excel(
                writer,
                sheet_name="Executive Summary",
                startrow=9,
                index=False,
            )
            clean_odds_df.to_excel(writer, sheet_name="Clean Odds", index=False)
            exceptions_df.to_excel(writer, sheet_name="DQ Exceptions Log", index=False)
            drift_df.to_excel(writer, sheet_name="Market Trends & Drift", index=False)

            summary_sheet = writer.book["Executive Summary"]
            summary_sheet.merge_cells("A1:B1")
            summary_sheet["A1"] = "Executive Summary"
            summary_sheet["A9"] = "Anomalies par règle"
            self._style_workbook(writer.book)

        LOGGER.info("Rapport qualité généré : %s", report_path)
        return report_path

    @staticmethod
    def _load_dataframes() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        with engine.connect() as connection:
            clean_odds_df = pd.read_sql(CLEAN_ODDS_QUERY, connection)
            exceptions_df = pd.read_sql(DQ_EXCEPTIONS_QUERY, connection)
        drift_df = MarketDriftAnalyzer().get_significant_drifts()
        return clean_odds_df, exceptions_df, drift_df

    @staticmethod
    def _format_payload(payload: object) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return payload
        return json.dumps(payload, ensure_ascii=False, default=str)

    @classmethod
    def _style_workbook(cls, workbook: Workbook) -> None:
        header_fill = PatternFill(fill_type="solid", fgColor="243447")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        title_fill = PatternFill(fill_type="solid", fgColor="15616D")
        title_font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
        thin_gray = Side(style="thin", color="D5DCE3")
        cell_border = Border(
            left=thin_gray,
            right=thin_gray,
            top=thin_gray,
            bottom=thin_gray,
        )

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2" if worksheet.title != "Executive Summary" else "A4"
            worksheet.sheet_view.showGridLines = False

            for row in worksheet.iter_rows():
                for cell in row:
                    cell.border = cell_border
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

            header_rows = [1]
            if worksheet.title == "Executive Summary":
                header_rows = [3, 10]
                worksheet["A1"].fill = title_fill
                worksheet["A1"].font = title_font
                worksheet["A1"].alignment = Alignment(vertical="center")
                worksheet.row_dimensions[1].height = 26

            for header_row in header_rows:
                if header_row > worksheet.max_row:
                    continue
                for cell in worksheet[header_row]:
                    if cell.value is not None:
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.alignment = Alignment(
                            horizontal="left",
                            vertical="center",
                            wrap_text=True,
                        )

            if worksheet.title == "Executive Summary":
                worksheet[7][1].number_format = "0.00%"

            if worksheet.title == "Clean Odds":
                for row_index in range(2, worksheet.max_row + 1):
                    worksheet.cell(row_index, 8).number_format = '0.00"%"'

            if worksheet.title == "Market Trends & Drift":
                variation_column = next(
                    cell.column
                    for cell in worksheet[1]
                    if cell.value == "Variation (%)"
                )
                column_letter = get_column_letter(variation_column)
                green_fill = PatternFill(fill_type="solid", fgColor="D9EAD3")
                orange_fill = PatternFill(fill_type="solid", fgColor="FCE4D6")
                for row_index in range(2, worksheet.max_row + 1):
                    worksheet.cell(row_index, variation_column).number_format = (
                        "+0.00%;-0.00%"
                    )
                if worksheet.max_row > 1:
                    drift_range = "{}2:{}{}".format(
                        column_letter,
                        column_letter,
                        worksheet.max_row,
                    )
                    worksheet.conditional_formatting.add(
                        drift_range,
                        FormulaRule(
                            formula=["${}2<0".format(column_letter)],
                            fill=green_fill,
                        ),
                    )
                    worksheet.conditional_formatting.add(
                        drift_range,
                        FormulaRule(
                            formula=["${}2>0".format(column_letter)],
                            fill=orange_fill,
                        ),
                    )

            cls._adjust_column_widths(worksheet)

        exception_sheet = workbook["DQ Exceptions Log"]
        if exception_sheet.max_row > 1:
            critical_fill = PatternFill(fill_type="solid", fgColor="FCE8E6")
            exception_sheet.conditional_formatting.add(
                "A2:F{}".format(exception_sheet.max_row),
                FormulaRule(
                    formula=['LEFT($C2,7)="DQ_ERR_"'],
                    fill=critical_fill,
                ),
            )

    @staticmethod
    def _adjust_column_widths(worksheet) -> None:
        for column_cells in worksheet.columns:
            column_letter = get_column_letter(column_cells[0].column)
            max_length = max(
                (len(str(cell.value)) for cell in column_cells if cell.value is not None),
                default=0,
            )
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    generated_path = ExcelReportGenerator().generate_report()
    print(generated_path)
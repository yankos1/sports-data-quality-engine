from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.config import DATABASE_URL


engine = create_engine(
    DATABASE_URL,
    pool_recycle=3600,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


def migrate_clean_odds_history_constraint(bind=None) -> None:
    """Ensure clean_odds is unique by fixture, bookmaker, and capture time."""
    database = bind or engine
    with database.begin() as connection:
        inspector = inspect(connection)
        unique_keys = {}
        for constraint in inspector.get_unique_constraints("clean_odds"):
            unique_keys[constraint.get("name")] = tuple(
                constraint.get("column_names") or ()
            )
        for index in inspector.get_indexes("clean_odds"):
            if index.get("unique"):
                unique_keys[index.get("name")] = tuple(index.get("column_names") or ())

        target_columns = ("fixture_id", "bookmaker", "captured_at")
        target_name = "uq_clean_odds_history"
        target_exists = unique_keys.get(target_name) == target_columns
        legacy_exists = any(
            columns == ("fixture_id", "bookmaker")
            for columns in unique_keys.values()
        )
        if target_exists and not legacy_exists:
            return
        if connection.dialect.name not in {"mysql", "mariadb"}:
            raise NotImplementedError(
                "La migration automatique de clean_odds est disponible uniquement pour MySQL."
            )

        quote = connection.dialect.identifier_preparer.quote
        for index_name, columns in unique_keys.items():
            if not index_name:
                continue
            if columns in {
                ("fixture_id", "bookmaker"),
                target_columns,
            }:
                connection.exec_driver_sql(
                    "ALTER TABLE clean_odds DROP INDEX {}".format(quote(index_name))
                )

        if not target_exists:
            connection.exec_driver_sql(
                "ALTER TABLE clean_odds ADD UNIQUE KEY {} "
                "(fixture_id, bookmaker, captured_at)".format(quote(target_name))
            )
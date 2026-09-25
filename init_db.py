import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.database.connection import engine
from src.database.models import Base


def main() -> int:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        print("Connexion MySQL réussie.")

        Base.metadata.create_all(bind=engine)
        print("Tables créées ou déjà à jour.")
        return 0
    except SQLAlchemyError as error:
        print("Échec de l'initialisation de la base MySQL: {}".format(error), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
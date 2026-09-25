import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_USER = os.getenv("DB_USER", "sports_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
MYSQL_HOST = os.getenv("MYSQL_HOST", os.getenv("DB_HOST", "127.0.0.1"))
MYSQL_PORT = int(os.getenv("MYSQL_PORT", os.getenv("DB_PORT", "3306")))
DB_NAME = os.getenv("DB_NAME", "sports_data")

DATABASE_URL = os.getenv("DATABASE_URL") or URL.create(
    drivername="mysql+pymysql",
    username=DB_USER,
    password=DB_PASSWORD,
    host=MYSQL_HOST,
    port=MYSQL_PORT,
    database=DB_NAME,
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
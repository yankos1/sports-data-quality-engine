from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.ingestion.golden_source as golden_source_module
from src.database.models import Base, Team, TeamAlias
from src.ingestion.golden_source import load_canonical_teams
from src.reconciliation.entity_resolver import EntityResolver


EXPECTED_SUPER_LEAGUE_2_TEAMS = {
    "AEL Larissa",
    "PAOK B",
    "Kalamaria",
    "Karditsa",
    "Niki Volos",
    "Chrisoupolis",
    "Panionios",
    "Zakynthos",
    "Ellas Syrou",
    "Pyrgos",
    "Panserraikos",
    "Olympiacos Piraeus B",
    "Athens Kallithea",
    "Asteras Tripolis B",
    "Marko",
    "Panthrakikos",
}


def test_super_league_2_seed_inserts_sixteen_teams_without_network(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        golden_source_module,
        "load_teams",
        lambda: (_ for _ in ()).throw(AssertionError("Le CSV ne doit pas être appelé.")),
    )

    inserted_count = load_canonical_teams(
        "super-league-2",
        session_factory=test_session_factory,
    )

    assert inserted_count == 16
    with test_session_factory() as session:
        team_names = set(session.scalars(select(Team.canonical_name)).all())

    assert team_names == EXPECTED_SUPER_LEAGUE_2_TEAMS
    assert load_canonical_teams(
        "super-league-2",
        session_factory=test_session_factory,
    ) == 0
    engine.dispose()


def test_future_league_json_seed_creates_fuzzy_alias(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    seed_path = tmp_path / "future-league_teams.json"
    seed_path.write_text('["Arsenall", "New FC"]', encoding="utf-8")
    monkeypatch.setattr(golden_source_module, "REFERENCE_DIR", tmp_path)

    with test_session_factory() as session:
        session.add(Team(canonical_name="Arsenal"))
        session.commit()

    assert load_canonical_teams(
        "future-league",
        session_factory=test_session_factory,
    ) == 1

    with test_session_factory() as session:
        alias = session.scalar(
            select(TeamAlias).where(TeamAlias.alias == "Arsenall")
        )
        resolver = EntityResolver(session=session)
        assert resolver.resolve("Arsenall")["canonical_name"] == "Arsenal"

    assert alias is not None
    engine.dispose()


def test_future_league_csv_seed_is_discovered(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    (tmp_path / "another-league_teams.csv").write_text(
        "team\nNorth FC\nSouth FC\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(golden_source_module, "REFERENCE_DIR", tmp_path)

    inserted_count = load_canonical_teams(
        "another-league",
        session_factory=test_session_factory,
    )

    assert inserted_count == 2
    with test_session_factory() as session:
        team_names = set(session.scalars(select(Team.canonical_name)).all())
    assert team_names == {"North FC", "South FC"}
    engine.dispose()

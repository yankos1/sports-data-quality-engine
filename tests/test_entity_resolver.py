import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.database.models import Base, Team, TeamAlias
from src.reconciliation.entity_resolver import EntityResolver


@pytest.fixture
def resolver_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False)
    with test_session_factory() as session:
        yield session
    engine.dispose()


def _seed_teams(session: Session, *canonical_names: str) -> None:
    session.add_all(Team(canonical_name=name) for name in canonical_names)
    session.flush()


@pytest.mark.parametrize(
    ("raw_name", "canonical_name"),
    [
        ("Manchester City", "Man City"),
        ("Nottingham Forest", "Nott'm Forest"),
    ],
)
def test_resolves_known_aliases_to_golden_source_names(
    resolver_session, raw_name, canonical_name
):
    _seed_teams(resolver_session, "Man City", "Nott'm Forest", "Arsenal")
    resolver = EntityResolver(session=resolver_session)

    result = resolver.resolve(raw_name)

    assert result["canonical_name"] == canonical_name
    assert result["method"] == "alias"
    assert result["confidence"] == 1.0


def test_resolves_case_insensitively_and_strips_surrounding_whitespace(resolver_session):
    _seed_teams(resolver_session, "Arsenal")
    resolver = EntityResolver(session=resolver_session)

    result = resolver.resolve("  arsenal  ")

    assert result["canonical_name"] == "Arsenal"
    assert result["method"] == "exact"
    assert result["confidence"] == 1.0


def test_typo_above_85_percent_is_resolved_and_persisted(resolver_session):
    _seed_teams(resolver_session, "Manchester City")
    resolver = EntityResolver(session=resolver_session)

    result = resolver.resolve("Manchester Cty")
    persisted_alias = resolver_session.scalar(
        select(TeamAlias).where(
            TeamAlias.normalized_alias == resolver._normalize("Manchester Cty")
        )
    )
    reloaded_resolver = EntityResolver(session=resolver_session)
    reloaded_result = reloaded_resolver.resolve("Manchester Cty")

    assert result["canonical_name"] == "Manchester City"
    assert result["method"] == "fuzzy"
    assert result["confidence"] >= 0.85
    assert persisted_alias is not None
    assert persisted_alias.team_id == resolver_session.scalar(
        select(Team.id).where(Team.canonical_name == "Manchester City")
    )
    assert reloaded_result["canonical_name"] == "Manchester City"
    assert reloaded_result["method"] == "alias"
    assert reloaded_result["confidence"] == 1.0


def test_intermediate_fuzzy_match_is_logged_but_not_persisted(
    resolver_session, caplog
):
    _seed_teams(resolver_session, "Manchester City")
    resolver = EntityResolver(session=resolver_session)

    with caplog.at_level("WARNING"):
        result = resolver.resolve("Manchester")

    assert result["canonical_name"] == "Manchester City"
    assert 0.65 <= result["confidence"] < 0.85
    assert "confiance intermédiaire" in caplog.text
    assert resolver_session.scalar(select(TeamAlias)) is None


def test_distant_team_is_rejected(resolver_session):
    _seed_teams(
        resolver_session,
        "Arsenal",
        "Man City",
        "Nott'm Forest",
        "Tottenham",
        "Liverpool",
        "Chelsea",
        "Manchester United",
        "Aston Villa",
        "Newcastle",
        "Brighton",
        "West Ham",
        "Wolves",
        "Everton",
        "Fulham",
        "Brentford",
        "Crystal Palace",
        "Bournemouth",
        "Leeds",
        "Ipswich",
        "Sunderland",
        "Burnley",
    )
    resolver = EntityResolver(session=resolver_session)

    result = resolver.resolve("Real Madrid")

    assert result["canonical_name"] is None
    assert result["method"] == "unresolved"
    assert result["confidence"] < 0.65
    assert resolver_session.scalar(select(TeamAlias)) is None
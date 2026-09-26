from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    home_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="home_team", foreign_keys="Fixture.home_team_id"
    )
    away_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="away_team", foreign_keys="Fixture.away_team_id"
    )


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_team_aliases_normalized_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint(
            "competition",
            "kickoff_at",
            "home_team_id",
            "away_team_id",
            name="uq_fixtures_competition_kickoff_teams",
        ),
        CheckConstraint("home_team_id <> away_team_id", name="ck_fixtures_distinct_teams"),
        Index("ix_fixtures_kickoff_at", "kickoff_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition: Mapped[str] = mapped_column(String(150), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    home_team: Mapped[Team] = relationship(
        back_populates="home_fixtures", foreign_keys=[home_team_id]
    )
    away_team: Mapped[Team] = relationship(
        back_populates="away_fixtures", foreign_keys=[away_team_id]
    )
    clean_odds: Mapped[list["CleanOdds"]] = relationship(back_populates="fixture")
    dq_exceptions: Mapped[list["DQException"]] = relationship(back_populates="fixture")


class CleanOdds(Base):
    __tablename__ = "clean_odds"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "bookmaker",
            "captured_at",
            name="uq_clean_odds_fixture_bookmaker_captured",
        ),
        CheckConstraint("odds_home > 1", name="ck_clean_odds_home_gt_one"),
        CheckConstraint("odds_draw > 1", name="ck_clean_odds_draw_gt_one"),
        CheckConstraint("odds_away > 1", name="ck_clean_odds_away_gt_one"),
        Index("ix_clean_odds_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    odds_home: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    odds_draw: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    odds_away: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    fixture: Mapped[Fixture] = relationship(back_populates="clean_odds")


class DQException(Base):
    __tablename__ = "dq_exceptions"
    __table_args__ = (
        Index("ix_dq_exceptions_rule_code_created_at", "rule_code", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fixtures.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    rejected_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    fixture: Mapped[Optional[Fixture]] = relationship(back_populates="dq_exceptions")
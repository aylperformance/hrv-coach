"""Modèles SQLite (SQLAlchemy 2.0) pour HRV Coach."""

from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer,
    String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


DB_PATH = os.environ.get("HRV_DB_PATH", str(Path(__file__).parent / "hrv_coach.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Athlete(Base):
    __tablename__ = "athletes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str] = mapped_column(String(1), default="M")  # "M" / "F"
    sport: Mapped[str] = mapped_column(String(80), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tests = relationship("Test", back_populates="athlete", cascade="all, delete-orphan",
                         order_by="Test.test_date.desc()")

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        a = (self.first_name[:1] or "").upper()
        b = (self.last_name[:1] or "").upper()
        return f"{a}{b}" or "?"


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False)
    test_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    source_filename: Mapped[str] = mapped_column(String(255), default="")
    source_type: Mapped[str] = mapped_column(String(20), default="txt")  # txt | kubios_csv
    comment: Mapped[str] = mapped_column(Text, default="")
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-10

    # Méta analyse
    n_beats_total: Mapped[int] = mapped_column(Integer, default=0)
    n_artifacts_removed: Mapped[int] = mapped_column(Integer, default=0)
    transition_index: Mapped[int] = mapped_column(Integer, default=0)
    transition_time_s: Mapped[float] = mapped_column(Float, default=0.0)
    total_duration_s: Mapped[float] = mapped_column(Float, default=0.0)

    # Classification
    fatigue_type: Mapped[str] = mapped_column(String(40), default="normal")
    fatigue_label: Mapped[str] = mapped_column(String(200), default="")
    fatigue_color: Mapped[str] = mapped_column(String(20), default="green")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")
    alerts_json: Mapped[str] = mapped_column(Text, default="[]")
    dfa_badge_json: Mapped[str] = mapped_column(Text, default="{}")
    delta_hr: Mapped[float] = mapped_column(Float, default=0.0)

    # Métriques assis (préfixe sit_)
    sit_mean_rr: Mapped[float] = mapped_column(Float, default=0.0)
    sit_mean_hr: Mapped[float] = mapped_column(Float, default=0.0)
    sit_min_hr: Mapped[float] = mapped_column(Float, default=0.0)
    sit_max_hr: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sdnn: Mapped[float] = mapped_column(Float, default=0.0)
    sit_rmssd: Mapped[float] = mapped_column(Float, default=0.0)
    sit_pnn50: Mapped[float] = mapped_column(Float, default=0.0)
    sit_vlf: Mapped[float] = mapped_column(Float, default=0.0)
    sit_lf: Mapped[float] = mapped_column(Float, default=0.0)
    sit_hf: Mapped[float] = mapped_column(Float, default=0.0)
    sit_total_power: Mapped[float] = mapped_column(Float, default=0.0)
    sit_lf_hf: Mapped[float] = mapped_column(Float, default=0.0)
    sit_lf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sit_hf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sit_vlf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sit_lf_nu: Mapped[float] = mapped_column(Float, default=0.0)
    sit_hf_nu: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sd1: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sd2: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sd2_sd1: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sampen: Mapped[float] = mapped_column(Float, default=0.0)
    sit_dfa_alpha1: Mapped[float] = mapped_column(Float, default=0.0)
    sit_stress_index: Mapped[float] = mapped_column(Float, default=0.0)
    sit_pns_index: Mapped[float] = mapped_column(Float, default=0.0)
    sit_sns_index: Mapped[float] = mapped_column(Float, default=0.0)
    sit_duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    sit_n_beats: Mapped[int] = mapped_column(Integer, default=0)

    # Métriques debout (préfixe std_)
    std_mean_rr: Mapped[float] = mapped_column(Float, default=0.0)
    std_mean_hr: Mapped[float] = mapped_column(Float, default=0.0)
    std_min_hr: Mapped[float] = mapped_column(Float, default=0.0)
    std_max_hr: Mapped[float] = mapped_column(Float, default=0.0)
    std_sdnn: Mapped[float] = mapped_column(Float, default=0.0)
    std_rmssd: Mapped[float] = mapped_column(Float, default=0.0)
    std_pnn50: Mapped[float] = mapped_column(Float, default=0.0)
    std_vlf: Mapped[float] = mapped_column(Float, default=0.0)
    std_lf: Mapped[float] = mapped_column(Float, default=0.0)
    std_hf: Mapped[float] = mapped_column(Float, default=0.0)
    std_total_power: Mapped[float] = mapped_column(Float, default=0.0)
    std_lf_hf: Mapped[float] = mapped_column(Float, default=0.0)
    std_lf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    std_hf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    std_vlf_pct: Mapped[float] = mapped_column(Float, default=0.0)
    std_lf_nu: Mapped[float] = mapped_column(Float, default=0.0)
    std_hf_nu: Mapped[float] = mapped_column(Float, default=0.0)
    std_sd1: Mapped[float] = mapped_column(Float, default=0.0)
    std_sd2: Mapped[float] = mapped_column(Float, default=0.0)
    std_sd2_sd1: Mapped[float] = mapped_column(Float, default=0.0)
    std_sampen: Mapped[float] = mapped_column(Float, default=0.0)
    std_dfa_alpha1: Mapped[float] = mapped_column(Float, default=0.0)
    std_stress_index: Mapped[float] = mapped_column(Float, default=0.0)
    std_pns_index: Mapped[float] = mapped_column(Float, default=0.0)
    std_sns_index: Mapped[float] = mapped_column(Float, default=0.0)
    std_duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    std_n_beats: Mapped[int] = mapped_column(Integer, default=0)

    athlete = relationship("Athlete", back_populates="tests")

    @property
    def recommendations(self) -> list[str]:
        try:
            return json.loads(self.recommendations_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def alerts(self) -> list[dict[str, str]]:
        try:
            return json.loads(self.alerts_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def dfa_badge(self) -> dict[str, str]:
        try:
            return json.loads(self.dfa_badge_json or "{}")
        except json.JSONDecodeError:
            return {}


def init_db() -> None:
    """Crée la base et les tables si nécessaire."""
    Base.metadata.create_all(bind=engine)


def get_session():
    """Générateur de session pour FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers de conversion AnalysisResult -> Test
# ---------------------------------------------------------------------------

SEGMENT_FIELDS = [
    "mean_rr", "mean_hr", "min_hr", "max_hr", "sdnn", "rmssd", "pnn50",
    "vlf", "lf", "hf", "total_power", "lf_hf", "lf_pct", "hf_pct", "vlf_pct",
    "lf_nu", "hf_nu", "sd1", "sd2", "sd2_sd1", "sampen", "dfa_alpha1",
    "stress_index", "pns_index", "sns_index", "duration_s", "n_beats",
]


def apply_analysis_to_test(test: Test, result: Any) -> None:
    """Reporte un AnalysisResult dans une instance Test (sans commit)."""
    test.n_beats_total = result.n_beats_total
    test.n_artifacts_removed = result.n_artifacts_removed
    test.transition_index = result.transition_index
    test.transition_time_s = result.transition_time_s
    test.total_duration_s = result.total_duration_s
    test.fatigue_type = result.fatigue_type
    test.fatigue_label = result.fatigue_label
    test.fatigue_color = result.fatigue_color
    test.delta_hr = result.delta_hr
    test.recommendations_json = json.dumps(result.recommendations, ensure_ascii=False)
    test.alerts_json = json.dumps(result.alerts, ensure_ascii=False)
    test.dfa_badge_json = json.dumps(result.dfa_badge, ensure_ascii=False)

    for f in SEGMENT_FIELDS:
        setattr(test, f"sit_{f}", getattr(result.sitting, f))
        setattr(test, f"std_{f}", getattr(result.standing, f))

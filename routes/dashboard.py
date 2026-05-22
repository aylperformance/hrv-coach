"""Routes — Page d'accueil et vue globale."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import Athlete, Test, get_session

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


# Tri par criticité : rouge d'abord, puis orange, puis vert
COLOR_ORDER = {"red": 0, "orange": 1, "green": 2, "dark_green": 3}


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    status: str = "",
    db: Session = Depends(get_session),
) -> HTMLResponse:
    athletes = db.query(Athlete).filter_by(archived=False).all()

    cards: list[dict[str, Any]] = []
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)

    for a in athletes:
        last_test = (
            db.query(Test).filter_by(athlete_id=a.id)
            .order_by(Test.test_date.desc()).first()
        )
        if last_test:
            color = last_test.fatigue_color
            label = last_test.fatigue_label
            sort_color = "red" if color == "red" else ("orange" if color == "orange" else "green")
        else:
            color = "neutral"
            label = "Aucun test"
            sort_color = "green"

        cards.append({
            "athlete": a,
            "last_test": last_test,
            "color": color,
            "sort_color": sort_color,
            "label": label,
        })

    # Filtre par statut
    if status in ("red", "orange", "green"):
        cards = [c for c in cards if c["sort_color"] == status]

    # Tri : statut criticité d'abord, puis nom
    cards.sort(key=lambda c: (
        COLOR_ORDER.get(c["sort_color"], 99),
        c["athlete"].last_name.lower(),
    ))

    # Snapshot du jour
    today_tests = (
        db.query(Test).filter(Test.test_date >= today_start).order_by(Test.test_date.desc()).all()
    )

    # Athlètes archivés (lien)
    archived_count = db.query(Athlete).filter_by(archived=True).count()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "cards": cards,
            "today_tests": today_tests,
            "status_filter": status,
            "archived_count": archived_count,
        },
    )


@router.get("/archived", response_class=HTMLResponse)
def archived(request: Request, db: Session = Depends(get_session)) -> HTMLResponse:
    athletes = (
        db.query(Athlete).filter_by(archived=True).order_by(Athlete.last_name).all()
    )
    return templates.TemplateResponse(
        request,
        "archived.html",
        {"athletes": athletes},
    )

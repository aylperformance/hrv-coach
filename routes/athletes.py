"""Routes — CRUD athlètes."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import Athlete, get_session

router = APIRouter(prefix="/athletes", tags=["athletes"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get("/new", response_class=HTMLResponse)
def new_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "athlete_form.html",
        {"athlete": None, "action": "/athletes/create", "title": "Nouvel athlète"},
    )


@router.post("/create")
def create(
    last_name: str = Form(...),
    first_name: str = Form(...),
    birth_date: str = Form(""),
    sex: str = Form("M"),
    sport: str = Form(""),
    db: Session = Depends(get_session),
) -> RedirectResponse:
    a = Athlete(
        last_name=last_name.strip(),
        first_name=first_name.strip(),
        birth_date=_parse_date(birth_date),
        sex=sex if sex in ("M", "F") else "M",
        sport=sport.strip(),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return RedirectResponse(url=f"/athletes/{a.id}", status_code=303)


@router.get("/{athlete_id}/edit", response_class=HTMLResponse)
def edit_form(athlete_id: int, request: Request, db: Session = Depends(get_session)) -> HTMLResponse:
    a = db.get(Athlete, athlete_id)
    if not a:
        raise HTTPException(404, "Athlète introuvable")
    return templates.TemplateResponse(
        request,
        "athlete_form.html",
        {"athlete": a,
         "action": f"/athletes/{a.id}/update", "title": f"Modifier {a.display_name}"},
    )


@router.post("/{athlete_id}/update")
def update(
    athlete_id: int,
    last_name: str = Form(...),
    first_name: str = Form(...),
    birth_date: str = Form(""),
    sex: str = Form("M"),
    sport: str = Form(""),
    db: Session = Depends(get_session),
) -> RedirectResponse:
    a = db.get(Athlete, athlete_id)
    if not a:
        raise HTTPException(404, "Athlète introuvable")
    a.last_name = last_name.strip()
    a.first_name = first_name.strip()
    a.birth_date = _parse_date(birth_date)
    a.sex = sex if sex in ("M", "F") else "M"
    a.sport = sport.strip()
    db.commit()
    return RedirectResponse(url=f"/athletes/{a.id}", status_code=303)


@router.post("/{athlete_id}/archive")
def archive(athlete_id: int, db: Session = Depends(get_session)) -> RedirectResponse:
    a = db.get(Athlete, athlete_id)
    if not a:
        raise HTTPException(404, "Athlète introuvable")
    a.archived = not a.archived
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.get("/{athlete_id}", response_class=HTMLResponse)
def detail(athlete_id: int, request: Request, db: Session = Depends(get_session)) -> HTMLResponse:
    a = db.get(Athlete, athlete_id)
    if not a:
        raise HTTPException(404, "Athlète introuvable")
    tests = sorted(a.tests, key=lambda t: t.test_date, reverse=True)
    return templates.TemplateResponse(
        request,
        "athlete.html",
        {"athlete": a, "tests": tests},
    )

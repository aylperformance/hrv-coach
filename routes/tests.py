"""Routes — Import et analyse des tests HRV."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import Athlete, Test, apply_analysis_to_test, get_session
from hrv_engine import (
    analyze_kubios_csv, analyze_rr_file, extract_date_from_filename,
    marker_color, parse_rr_txt,
)

router = APIRouter(prefix="/tests", tags=["tests"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# Cache mémoire des aperçus (clé : token, valeur : dict)
PREVIEW_CACHE: dict[str, dict[str, Any]] = {}


@router.get("/new", response_class=HTMLResponse)
def new_form(
    request: Request,
    athlete_id: int | None = None,
    db: Session = Depends(get_session),
) -> HTMLResponse:
    athletes = db.query(Athlete).filter_by(archived=False).order_by(Athlete.last_name).all()
    selected = db.get(Athlete, athlete_id) if athlete_id else None
    return templates.TemplateResponse(
        request,
        "test_import.html",
        {"athletes": athletes, "selected": selected},
    )


@router.get("/bulk", response_class=HTMLResponse)
def bulk_form(
    request: Request,
    athlete_id: int | None = None,
    db: Session = Depends(get_session),
) -> HTMLResponse:
    """Formulaire d'import multiple."""
    athletes = db.query(Athlete).filter_by(archived=False).order_by(Athlete.last_name).all()
    selected = db.get(Athlete, athlete_id) if athlete_id else None
    return templates.TemplateResponse(
        request,
        "test_bulk.html",
        {"athletes": athletes, "selected": selected},
    )


@router.post("/single_upload")
async def single_upload(
    file: UploadFile = File(...),
    athlete_id: int = Form(...),
    correct_artifacts: str = Form(""),
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Upload + analyse + sauvegarde d'UN seul fichier (utilisé par bulk côté client).

    Appelé en boucle par le navigateur en mode bulk. Évite de bloquer le
    worker uvicorn trop longtemps (sinon Railway tue le container).
    """
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        raise HTTPException(404, "Athlète introuvable")

    do_correct = correct_artifacts in ("1", "true", "on", "yes")
    fname = file.filename or "upload.txt"

    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")

    extracted_dt = extract_date_from_filename(fname)
    if not extracted_dt:
        return JSONResponse({
            "status": "skipped",
            "filename": fname,
            "message": "Date non détectable dans le nom de fichier",
        })

    existing = (
        db.query(Test)
        .filter(Test.athlete_id == athlete.id)
        .filter(Test.test_date == extracted_dt)
        .first()
    )
    if existing:
        return JSONResponse({
            "status": "skipped",
            "filename": fname,
            "message": f"Test déjà existant ({extracted_dt.strftime('%d/%m/%Y %H:%M')})",
            "test_id": existing.id,
        })

    suffix = Path(fname).suffix.lower()
    try:
        if suffix == ".csv" or "RESULTS FOR SINGLE SAMPLES" in content.upper():
            source_type = "kubios_csv"
            result = analyze_kubios_csv(content)
            if result is None:
                return JSONResponse({"status": "error", "filename": fname,
                                     "message": "CSV Kubios non parsable"})
        else:
            source_type = "txt"
            rr = parse_rr_txt(content)
            if len(rr) < 50:
                return JSONResponse({"status": "error", "filename": fname,
                                     "message": f"Trop court ({len(rr)} beats)"})
            result = analyze_rr_file(rr, clean=do_correct)

        test = Test(
            athlete_id=athlete.id,
            test_date=extracted_dt,
            source_filename=fname,
            source_type=source_type,
            comment="",
            rpe=None,
        )
        apply_analysis_to_test(test, result)
        db.add(test)
        db.commit()
        db.refresh(test)

        return JSONResponse({
            "status": "ok",
            "filename": fname,
            "test_id": test.id,
            "fatigue_color": result.fatigue_color,
            "message": (
                f"{extracted_dt.strftime('%d/%m/%Y %H:%M')} · "
                f"{result.fatigue_label} · ΔFC {result.delta_hr:.0f} bpm"
            ),
        })
    except Exception as e:
        db.rollback()
        return JSONResponse({
            "status": "error",
            "filename": fname,
            "message": f"{type(e).__name__} - {str(e)[:120]}",
        })


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    athlete_id: int = Form(...),
    correct_artifacts: str = Form(""),
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Analyse le fichier et renvoie un aperçu JSON (sans persister)."""
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        raise HTTPException(404, "Athlète introuvable")

    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")

    fname = file.filename or "upload.txt"
    suffix = Path(fname).suffix.lower()

    # Date depuis nom de fichier
    extracted_dt = extract_date_from_filename(fname)

    source_type = "txt"
    rr = None
    result = None

    do_correct = correct_artifacts in ("1", "true", "on", "yes")

    if suffix == ".csv" or "RESULTS FOR SINGLE SAMPLES" in content.upper():
        source_type = "kubios_csv"
        result = analyze_kubios_csv(content)
        if result is None:
            raise HTTPException(400, "Fichier CSV Kubios non parsable.")
    else:
        rr = parse_rr_txt(content)
        if len(rr) < 50:
            raise HTTPException(400, f"Fichier RR trop court ({len(rr)} battements lus).")
        result = analyze_rr_file(rr, clean=do_correct)

    # Stocker en cache pour confirmation
    token = secrets.token_urlsafe(16)
    PREVIEW_CACHE[token] = {
        "content": content,
        "filename": fname,
        "source_type": source_type,
        "athlete_id": athlete_id,
        "extracted_dt": extracted_dt.isoformat() if extracted_dt else None,
        "correct_artifacts": do_correct,
    }

    # Sérialiser le résultat pour le front
    payload = {
        "token": token,
        "filename": fname,
        "source_type": source_type,
        "extracted_date": extracted_dt.isoformat() if extracted_dt else None,
        "result": result.to_dict(),
        "rr_preview": rr[:2400].tolist() if rr is not None else None,
    }
    return JSONResponse(payload)


@router.post("/confirm")
def confirm(
    token: str = Form(...),
    test_date: str = Form(""),
    comment: str = Form(""),
    rpe: str = Form(""),
    transition_index: str = Form(""),
    db: Session = Depends(get_session),
) -> RedirectResponse:
    cached = PREVIEW_CACHE.pop(token, None)
    if not cached:
        raise HTTPException(400, "Aperçu expiré ou introuvable, recommencer l'import.")
    athlete = db.get(Athlete, cached["athlete_id"])
    if not athlete:
        raise HTTPException(404, "Athlète introuvable")

    # Re-analyser (si l'utilisateur a corrigé le point de transition)
    if cached["source_type"] == "kubios_csv":
        result = analyze_kubios_csv(cached["content"])
    else:
        rr = parse_rr_txt(cached["content"])
        tidx = None
        if transition_index:
            try:
                tidx = int(transition_index)
            except ValueError:
                tidx = None
        result = analyze_rr_file(
            rr,
            transition_idx=tidx,
            clean=cached.get("correct_artifacts", False),
        )

    if result is None:
        raise HTTPException(400, "Erreur d'analyse.")

    # Date
    dt: datetime | None = None
    if test_date:
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(test_date, fmt)
                break
            except ValueError:
                continue
    if dt is None and cached.get("extracted_dt"):
        try:
            dt = datetime.fromisoformat(cached["extracted_dt"])
        except ValueError:
            dt = datetime.utcnow()
    if dt is None:
        dt = datetime.utcnow()

    rpe_val: int | None = None
    if rpe:
        try:
            rpe_val = max(1, min(10, int(rpe)))
        except ValueError:
            rpe_val = None

    test = Test(
        athlete_id=athlete.id,
        test_date=dt,
        source_filename=cached["filename"],
        source_type=cached["source_type"],
        comment=comment.strip(),
        rpe=rpe_val,
    )
    apply_analysis_to_test(test, result)
    db.add(test)
    db.commit()
    db.refresh(test)
    return RedirectResponse(url=f"/tests/{test.id}", status_code=303)


@router.get("/{test_id}", response_class=HTMLResponse)
def detail(test_id: int, request: Request, db: Session = Depends(get_session)) -> HTMLResponse:
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(404, "Test introuvable")

    # Codes couleur des marqueurs clés pour le tableau
    colors = {
        "sit_rmssd": marker_color("rmssd", test.sit_rmssd, "sitting"),
        "sit_hf": marker_color("hf", test.sit_hf, "sitting"),
        "std_lf_hf": marker_color("lf_hf", test.std_lf_hf, "standing"),
        "delta_hr": marker_color("delta_hr", test.delta_hr),
        "sit_sampen": marker_color("sampen", test.sit_sampen, "sitting"),
        "sit_dfa_alpha1": marker_color("dfa_alpha1", test.sit_dfa_alpha1, "sitting"),
    }

    # Médiane 30 derniers jours sur l'athlète (exclut le test courant pour comparer)
    from datetime import timedelta
    cutoff = test.test_date - timedelta(days=30)
    history = (
        db.query(Test)
        .filter(Test.athlete_id == test.athlete_id)
        .filter(Test.test_date >= cutoff)
        .filter(Test.test_date <= test.test_date)
        .all()
    )

    def _median(vals: list[float]) -> float:
        vals = [v for v in vals if v is not None]
        if not vals:
            return 0.0
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    median = {
        "sit_lf": _median([t.sit_lf for t in history]),
        "sit_hf": _median([t.sit_hf for t in history]),
        "sit_mean_hr": _median([t.sit_mean_hr for t in history]),
        "std_lf": _median([t.std_lf for t in history]),
        "std_hf": _median([t.std_hf for t in history]),
        "std_mean_hr": _median([t.std_mean_hr for t in history]),
        "n_tests": len(history),
    }

    return templates.TemplateResponse(
        request,
        "test_detail.html",
        {"test": test, "athlete": test.athlete, "colors": colors, "median": median},
    )


@router.post("/{test_id}/delete")
def delete(test_id: int, db: Session = Depends(get_session)) -> RedirectResponse:
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(404, "Test introuvable")
    athlete_id = test.athlete_id
    db.delete(test)
    db.commit()
    return RedirectResponse(url=f"/athletes/{athlete_id}", status_code=303)


@router.get("/api/athlete/{athlete_id}/history")
def athlete_history_api(athlete_id: int, db: Session = Depends(get_session)) -> JSONResponse:
    """API JSON pour le graphique d'évolution."""
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        raise HTTPException(404, "Athlète introuvable")
    tests = sorted(athlete.tests, key=lambda t: t.test_date)

    series: list[dict[str, Any]] = []
    for t in tests:
        series.append({
            "id": t.id,
            "date": t.test_date.isoformat(),
            "sit_rmssd": t.sit_rmssd,
            "sit_hf": t.sit_hf,
            "sit_lf_hf": t.sit_lf_hf,
            "std_lf_hf": t.std_lf_hf,
            "delta_hr": t.delta_hr,
            "sit_mean_hr": t.sit_mean_hr,
            "std_mean_hr": t.std_mean_hr,
            "sit_sampen": t.sit_sampen,
            "sit_dfa_alpha1": t.sit_dfa_alpha1,
            "sit_pns_index": t.sit_pns_index,
            "sit_sns_index": t.sit_sns_index,
            "fatigue_type": t.fatigue_type,
            "fatigue_color": t.fatigue_color,
            "comment": t.comment,
            "rpe": t.rpe,
        })

    # Médianes glissantes 30 jours pour quelques marqueurs
    return JSONResponse({
        "athlete": {"id": athlete.id, "name": athlete.display_name, "sport": athlete.sport},
        "tests": series,
    })

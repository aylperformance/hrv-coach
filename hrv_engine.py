"""
HRV Engine — Calcul complet des marqueurs HRV et classification de fatigue.

Pipeline :
  1. Parsing RR (txt brut, csv Kubios)
  2. Nettoyage des artefacts (filtre Malik)
  3. Détection automatique de la transition assis/debout
  4. Exclusion des 60 premières secondes de chaque segment
  5. Calcul temporel, fréquentiel (Welch FFT), non-linéaire
  6. Indices synthétiques PNS / SNS
  7. Classification Freville (types 1 à 5 + alertes)
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import interpolate, signal


# =============================================================================
# Constantes
# =============================================================================

FS_INTERP = 4.0  # Hz, fréquence d'interpolation pour FFT
VLF_BAND = (0.003, 0.04)
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.40)

EXCLUDE_SECONDS = 60.0  # Exclusion 1ère minute après transition
MIN_SEGMENT_SECONDS = 120.0  # Minimum pour analyse fiable


# =============================================================================
# Dataclasses de résultats
# =============================================================================

@dataclass
class SegmentMetrics:
    """Marqueurs HRV d'un segment (assis ou debout)."""
    # Temporel
    mean_rr: float = 0.0
    mean_hr: float = 0.0
    min_hr: float = 0.0
    max_hr: float = 0.0
    sdnn: float = 0.0
    rmssd: float = 0.0
    pnn50: float = 0.0
    # Fréquentiel
    vlf: float = 0.0
    lf: float = 0.0
    hf: float = 0.0
    total_power: float = 0.0
    lf_hf: float = 0.0
    lf_pct: float = 0.0
    hf_pct: float = 0.0
    vlf_pct: float = 0.0
    lf_nu: float = 0.0
    hf_nu: float = 0.0
    # Non-linéaire
    sd1: float = 0.0
    sd2: float = 0.0
    sd2_sd1: float = 0.0
    sampen: float = 0.0
    dfa_alpha1: float = 0.0
    stress_index: float = 0.0
    # Synthétiques
    pns_index: float = 0.0
    sns_index: float = 0.0
    # Méta
    duration_s: float = 0.0
    n_beats: int = 0


@dataclass
class AnalysisResult:
    """Résultat complet d'une analyse de test HRV."""
    sitting: SegmentMetrics
    standing: SegmentMetrics
    delta_hr: float = 0.0
    transition_index: int = 0
    transition_time_s: float = 0.0
    total_duration_s: float = 0.0
    n_beats_total: int = 0
    n_artifacts_removed: int = 0
    fatigue_type: str = ""
    fatigue_label: str = ""
    fatigue_color: str = "green"  # green / orange / red / dark_green
    recommendations: list[str] = field(default_factory=list)
    alerts: list[dict[str, str]] = field(default_factory=list)
    dfa_badge: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# =============================================================================
# Parsing des fichiers
# =============================================================================

DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[\s_T-]+(\d{2})[-:](\d{2})(?:[-:](\d{2}))?)?")


def extract_date_from_filename(filename: str) -> datetime | None:
    """Extrait la date du nom de fichier (YYYY-MM-DD[_HH-MM-SS])."""
    name = Path(filename).stem
    m = DATE_PATTERN.search(name)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)[0:4]), int(m.group(1)[5:7]), int(m.group(1)[8:10])
        h = int(m.group(2)) if m.group(2) else 0
        mi = int(m.group(3)) if m.group(3) else 0
        s = int(m.group(4)) if m.group(4) else 0
        return datetime(y, mo, d, h, mi, s)
    except (ValueError, TypeError):
        return None


def parse_rr_txt(content: str) -> np.ndarray:
    """Parse un fichier .txt d'intervalles RR (un par ligne, en ms).
    Accepte séparateurs : retour ligne, virgule, point-virgule, espace.
    """
    raw = re.split(r"[\s,;]+", content.strip())
    rr = []
    for token in raw:
        token = token.strip()
        if not token:
            continue
        try:
            v = float(token.replace(",", "."))
            if 250.0 <= v <= 2500.0:  # Bornes physiologiques larges
                rr.append(v)
        except ValueError:
            continue
    return np.array(rr, dtype=float)


def parse_kubios_csv(content: str) -> dict[str, dict[str, float]] | None:
    """Parse un .csv Kubios Scientific Lite — sections SAMPLE 1 (couché) et SAMPLE 2 (debout).

    Retourne {"sitting": {...}, "standing": {...}} ou None si non parsable.
    """
    lines = content.splitlines()
    # On cherche les colonnes SAMPLE 1 et SAMPLE 2 dans la zone RESULTS
    in_results = False
    headers: list[str] = []
    samples: dict[str, dict[str, float]] = {}

    for line in lines:
        if "RESULTS FOR SINGLE SAMPLES" in line.upper():
            in_results = True
            continue
        if not in_results:
            continue
        # Ligne d'entête samples
        if "SAMPLE 1" in line.upper() and "SAMPLE 2" in line.upper():
            headers = [c.strip() for c in re.split(r"[,;]", line)]
            continue
        # Ligne de données : "Parameter, value1, value2"
        parts = [p.strip() for p in re.split(r"[,;]", line)]
        if len(parts) < 3:
            continue
        param = parts[0].strip().strip('"')
        if not param:
            continue
        try:
            v1 = float(parts[1].replace(",", ".")) if parts[1] else None
            v2 = float(parts[2].replace(",", ".")) if parts[2] else None
        except ValueError:
            continue
        if v1 is None or v2 is None:
            continue
        samples.setdefault("sitting", {})[param] = v1
        samples.setdefault("standing", {})[param] = v2

    if not samples:
        return None
    return samples


# =============================================================================
# Nettoyage artefacts
# =============================================================================
#
# Stratégie en 3 couches (filtre physiologique) :
#   1. Bornes absolues RR : 300 < RR < 2000 ms (HR entre 30 et 200 bpm)
#      → élimine les valeurs physiologiquement impossibles
#   2. Bornes absolues sur les diffs successifs : |RR[i] - RR[i-1]| < 400 ms
#      → élimine les artefacts évidents (battement manqué, double détection)
#   3. Filtre Malik en complément : variation > threshold (par défaut 30 %)
#      → élimine les artefacts subtils dans les valeurs plausibles
#
# Limites physiologiques :
#   - Diff RR-RR > 300 ms : très rare (vagal extrême)
#   - Diff RR-RR > 400 ms : quasi-certainement artefact
#   - RMSSD max documenté en élite endurance/repos profond : ~250 ms

# Constantes physiologiques
RR_MIN_MS = 300.0   # HR < 200 bpm
RR_MAX_MS = 2000.0  # HR > 30 bpm
DIFF_MAX_MS = 400.0  # Diff RR-RR successive maximum plausible


def clean_rr_physiological(
    rr: np.ndarray,
    diff_max_ms: float = DIFF_MAX_MS,
    rr_min_ms: float = RR_MIN_MS,
    rr_max_ms: float = RR_MAX_MS,
    malik_threshold: float = 0.30,
) -> tuple[np.ndarray, int]:
    """Filtre physiologique en 3 couches.

    Retourne (rr_clean, n_corrected).
    Les valeurs corrigées sont remplacées par interpolation linéaire.
    """
    if len(rr) < 3:
        return rr.astype(float).copy(), 0

    rr_clean = rr.astype(float).copy()
    invalid = np.zeros(len(rr_clean), dtype=bool)

    # Couche 1 : bornes absolues sur le RR
    invalid |= (rr_clean < rr_min_ms) | (rr_clean > rr_max_ms)

    # Couche 2 : diff absolue avec le voisin précédent VALIDE
    # On itère pour gérer les artefacts consécutifs (ex : 1271 → 627)
    last_valid = None
    for i in range(len(rr_clean)):
        if invalid[i]:
            continue
        if last_valid is None:
            last_valid = i
            continue
        if abs(rr_clean[i] - rr_clean[last_valid]) > diff_max_ms:
            invalid[i] = True
        else:
            last_valid = i

    # Couche 3 : Malik (variation relative > seuil) sur les beats encore valides
    last_valid = None
    for i in range(len(rr_clean)):
        if invalid[i]:
            continue
        if last_valid is None:
            last_valid = i
            continue
        prev = rr_clean[last_valid]
        if prev > 0:
            delta = abs(rr_clean[i] - prev) / prev
            if delta > malik_threshold:
                invalid[i] = True
                continue
        last_valid = i

    n_corrected = int(invalid.sum())

    # Interpolation linéaire pour remplacer les invalides
    if n_corrected > 0:
        idx = np.arange(len(rr_clean))
        valid = ~invalid
        if valid.sum() >= 2:
            rr_clean[invalid] = np.interp(idx[invalid], idx[valid], rr_clean[valid])
        else:
            rr_clean[invalid] = np.nanmean(rr_clean[valid]) if valid.sum() else 1000.0

    return rr_clean, n_corrected


def clean_rr_malik(rr: np.ndarray, threshold: float = 0.20) -> tuple[np.ndarray, int]:
    """Filtre Malik historique : variation relative > threshold seulement.
    Conservé pour rétro-compatibilité — préférer clean_rr_physiological.
    """
    if len(rr) < 3:
        return rr.copy(), 0
    rr_clean = rr.astype(float).copy()
    n_removed = 0
    for i in range(1, len(rr_clean)):
        if rr_clean[i - 1] <= 0:
            continue
        delta = abs(rr_clean[i] - rr_clean[i - 1]) / rr_clean[i - 1]
        if delta > threshold:
            rr_clean[i] = np.nan
            n_removed += 1
    if np.any(np.isnan(rr_clean)):
        idx = np.arange(len(rr_clean))
        valid = ~np.isnan(rr_clean)
        if valid.sum() >= 2:
            rr_clean[~valid] = np.interp(idx[~valid], idx[valid], rr_clean[valid])
        else:
            rr_clean[~valid] = np.nanmean(rr_clean)
    return rr_clean, n_removed


# =============================================================================
# Détection de transition assis/debout
# =============================================================================

def detect_transition(rr: np.ndarray, min_time_s: float = 240.0) -> int:
    """Détecte l'index de transition assis→debout.

    Méthode principale : FC glissante 20 beats, on cherche le passage
    de < 70 bpm (sur 10 beats) à > 75 bpm (sur 20 beats) après t > min_time_s.
    Fallback : RR moyen passant de > 900 ms à < 800 ms.
    Si rien : retourne le milieu du fichier.
    """
    if len(rr) < 40:
        return len(rr) // 2

    # Temps cumulé en secondes pour chaque battement
    t_cum = np.cumsum(rr) / 1000.0

    # FC instantanée
    hr = 60000.0 / rr  # bpm

    # Glissantes
    win_short = 10
    win_long = 20
    hr_long = _rolling_mean(hr, win_long)
    hr_short = _rolling_mean(hr, win_short)

    # Recherche après min_time_s
    start_idx = int(np.searchsorted(t_cum, min_time_s))
    if start_idx >= len(rr) - win_long:
        start_idx = max(0, len(rr) // 2 - win_long)

    # Critère 1 : transition HR
    for i in range(start_idx, len(rr) - win_long):
        if i < win_short:
            continue
        prev_hr = hr_short[i - win_short:i].mean() if i >= win_short else np.nan
        cur_hr = hr_long[i:i + win_long].mean() if i + win_long <= len(hr_long) else np.nan
        if np.isnan(prev_hr) or np.isnan(cur_hr):
            continue
        if prev_hr < 70.0 and cur_hr > 75.0:
            return i

    # Critère 2 (fallback) : transition RR
    rr_smooth = _rolling_mean(rr, win_long)
    for i in range(start_idx, len(rr) - win_long):
        if i < win_short:
            continue
        prev = rr[max(0, i - win_short):i].mean()
        cur = rr_smooth[i:i + win_long].mean() if i + win_long <= len(rr_smooth) else np.nan
        if np.isnan(cur):
            continue
        if prev > 900.0 and cur < 800.0:
            return i

    # Défaut : milieu du fichier
    return len(rr) // 2


def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    """Moyenne glissante centrée, mêmes dimensions, bords répliqués."""
    if w <= 1:
        return x.copy()
    kernel = np.ones(w) / w
    return np.convolve(x, kernel, mode="same")


def split_segments(
    rr: np.ndarray,
    transition_idx: int,
    exclude_s: float = EXCLUDE_SECONDS,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Découpe en (sitting, standing) après exclusion d'EXCLUDE_SECONDS de chaque côté.

    Retourne (sitting_rr, standing_rr, transition_time_s).
    """
    if transition_idx <= 0 or transition_idx >= len(rr):
        transition_idx = len(rr) // 2

    sitting_full = rr[:transition_idx]
    standing_full = rr[transition_idx:]

    t_sit = np.cumsum(sitting_full) / 1000.0
    transition_time_s = t_sit[-1] if len(t_sit) else 0.0

    # Exclure les 60 premières secondes du segment assis (début) — on PEUT garder le début
    # Le protocole dit : exclure 60s après le DÉBUT de chaque position.
    # Donc pour assis : exclure 60s au début (0 à 60s)
    # Pour debout : exclure 60s juste après la transition.
    if len(sitting_full):
        t_from_start_sit = np.cumsum(sitting_full) / 1000.0
        sit_keep = t_from_start_sit >= exclude_s
        sitting = sitting_full[sit_keep] if sit_keep.any() else sitting_full
    else:
        sitting = sitting_full

    if len(standing_full):
        t_from_trans = np.cumsum(standing_full) / 1000.0
        stand_keep = t_from_trans >= exclude_s
        standing = standing_full[stand_keep] if stand_keep.any() else standing_full
    else:
        standing = standing_full

    return sitting, standing, transition_time_s


# =============================================================================
# Détendage Smoothn Priors (Tarvainen 2002) — utilisé par Kubios
# =============================================================================

def detrend_smoothn_priors(y: np.ndarray, lam: float = 500.0) -> np.ndarray:
    """Détendage par Smoothness Priors (Tarvainen, Ranta-aho, Karjalainen 2002).

    Sépare une tendance lente d'un signal stationnaire :
       trend = (I + λ² D₂ᵀ D₂)⁻¹ · y
       stationary = y - trend

    Avec λ=500 (réglage Kubios par défaut), ça retire les composantes basses
    fréquences (< ~0.035 Hz) typiques d'une dérive lente ou d'une transition
    posturale. Utilisé pour le SDNN et l'analyse fréquentielle.

    Reference: Tarvainen et al., IEEE Trans Biomed Eng, 49(2):172-175, 2002.
    """
    n = len(y)
    if n < 5:
        return y - np.mean(y)
    # Matrice D₂ de seconde dérivée (n-2 × n)
    D2 = np.zeros((n - 2, n))
    idx = np.arange(n - 2)
    D2[idx, idx] = 1.0
    D2[idx, idx + 1] = -2.0
    D2[idx, idx + 2] = 1.0
    # (I + λ² D₂ᵀ D₂)⁻¹ y
    I = np.eye(n)
    A = I + (lam ** 2) * (D2.T @ D2)
    try:
        trend = np.linalg.solve(A, y)
    except np.linalg.LinAlgError:
        return y - np.mean(y)
    return y - trend


# =============================================================================
# Calcul temporel
# =============================================================================

def compute_time_domain(rr: np.ndarray, detrend: bool = True) -> dict[str, float]:
    """Marqueurs temporels.

    SDNN est calculé sur le signal détendé par Smoothn priors λ=30 (retire
    les dérives lentes < 0.13 Hz). C'est plus agressif que λ=500 utilisé
    pour la FFT, car SDNN n'a pas à préserver le contenu fréquentiel : on
    veut juste éliminer les composantes lentes qui ne reflètent pas l'activité
    autonome (transition posturale, sudation, etc.).

    RMSSD et pNN50 sont calculés sur les diffs successives, donc insensibles
    au détendage.
    """
    if len(rr) < 2:
        return {"mean_rr": 0, "mean_hr": 0, "min_hr": 0, "max_hr": 0,
                "sdnn": 0, "rmssd": 0, "pnn50": 0}
    hr = 60000.0 / rr
    diff = np.diff(rr)
    if detrend and len(rr) >= 5:
        rr_d = detrend_smoothn_priors(rr.astype(float), lam=30.0)
        sdnn = float(np.std(rr_d, ddof=1))
    else:
        sdnn = float(np.std(rr, ddof=1))
    return {
        "mean_rr": float(np.mean(rr)),
        "mean_hr": float(np.mean(hr)),
        "min_hr": float(np.min(hr)),
        "max_hr": float(np.max(hr)),
        "sdnn": sdnn,
        "rmssd": float(np.sqrt(np.mean(diff ** 2))),
        "pnn50": float(100.0 * np.sum(np.abs(diff) > 50.0) / len(diff)),
    }


# =============================================================================
# Calcul fréquentiel (Welch FFT après interpolation à 4 Hz)
# =============================================================================

def compute_frequency_domain(rr: np.ndarray) -> dict[str, float]:
    if len(rr) < 30:
        return {"vlf": 0, "lf": 0, "hf": 0, "total_power": 0, "lf_hf": 0,
                "lf_pct": 0, "hf_pct": 0, "vlf_pct": 0, "lf_nu": 0, "hf_nu": 0}

    # Tachogramme : temps cumulé en secondes, RR en ms
    t = np.cumsum(rr) / 1000.0
    t = t - t[0]  # commence à 0

    # Interpolation cubique à 4 Hz
    total_s = t[-1]
    if total_s < 30:
        return {"vlf": 0, "lf": 0, "hf": 0, "total_power": 0, "lf_hf": 0,
                "lf_pct": 0, "hf_pct": 0, "vlf_pct": 0, "lf_nu": 0, "hf_nu": 0}

    t_uniform = np.arange(0, total_s, 1.0 / FS_INTERP)
    try:
        f_interp = interpolate.interp1d(t, rr, kind="cubic", bounds_error=False, fill_value="extrapolate")
        rr_uniform = f_interp(t_uniform)
    except Exception:
        f_interp = interpolate.interp1d(t, rr, kind="linear", bounds_error=False, fill_value="extrapolate")
        rr_uniform = f_interp(t_uniform)

    # Détendage Smoothn priors (Tarvainen λ=500, comme Kubios)
    rr_uniform = detrend_smoothn_priors(rr_uniform, lam=500.0)

    # Welch : segments de 256 s ou moins selon disponibilité
    nperseg = min(256 * int(FS_INTERP), len(rr_uniform))
    if nperseg < 64:
        nperseg = len(rr_uniform)
    freqs, psd = signal.welch(rr_uniform, fs=FS_INTERP, nperseg=nperseg,
                              window="hann", scaling="density")

    def band_power(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            return 0.0
        return float(np.trapezoid(psd[mask], freqs[mask]))

    vlf = band_power(*VLF_BAND)
    lf = band_power(*LF_BAND)
    hf = band_power(*HF_BAND)
    total = vlf + lf + hf

    lf_hf = lf / hf if hf > 0 else 0.0
    lf_nu = 100.0 * lf / (lf + hf) if (lf + hf) > 0 else 0.0
    hf_nu = 100.0 * hf / (lf + hf) if (lf + hf) > 0 else 0.0
    lf_pct = 100.0 * lf / total if total > 0 else 0.0
    hf_pct = 100.0 * hf / total if total > 0 else 0.0
    vlf_pct = 100.0 * vlf / total if total > 0 else 0.0

    return {
        "vlf": vlf, "lf": lf, "hf": hf, "total_power": total,
        "lf_hf": lf_hf, "lf_pct": lf_pct, "hf_pct": hf_pct, "vlf_pct": vlf_pct,
        "lf_nu": lf_nu, "hf_nu": hf_nu,
    }


# =============================================================================
# Calcul non-linéaire
# =============================================================================

def compute_poincare(rr: np.ndarray) -> dict[str, float]:
    """Poincaré SD1 / SD2 / SD2/SD1.

    SD1 = std(RR[i] - RR[i+1]) / sqrt(2) — insensible au détendage (diff locale)
    SD2 = std(RR[i] + RR[i+1]) / sqrt(2) — sensible au détendage (somme)

    On applique Smoothn priors λ=30 sur le signal pour SD2, comme pour SDNN.
    """
    if len(rr) < 3:
        return {"sd1": 0, "sd2": 0, "sd2_sd1": 0}
    rr_d = detrend_smoothn_priors(rr.astype(float), lam=30.0) if len(rr) >= 5 else rr.astype(float)
    x = rr_d[:-1]
    y = rr_d[1:]
    sd1 = float(np.std(x - y, ddof=1) / math.sqrt(2))
    sd2 = float(np.std(x + y, ddof=1) / math.sqrt(2))
    ratio = sd2 / sd1 if sd1 > 0 else 0.0
    return {"sd1": sd1, "sd2": sd2, "sd2_sd1": ratio}


def compute_sampen(rr: np.ndarray, m: int = 2, r_factor: float = 0.2) -> float:
    """Sample Entropy (m=2, r=0.2*SD)."""
    n = len(rr)
    if n < m + 2:
        return 0.0
    r = r_factor * np.std(rr, ddof=1)
    if r <= 0:
        return 0.0

    def _phi(m_val: int) -> int:
        # Compte les paires de vecteurs de longueur m_val ayant une distance Chebyshev <= r
        # Astuce : on construit la matrice des distances par broadcast (peut être lourd
        # pour n > 5000, mais nos segments font ~300-500 beats)
        templates = np.array([rr[i:i + m_val] for i in range(n - m_val)])
        count = 0
        for i in range(len(templates)):
            d = np.max(np.abs(templates - templates[i]), axis=1)
            count += np.sum(d <= r) - 1  # -1 pour exclure soi-même
        return count

    try:
        b = _phi(m)
        a = _phi(m + 1)
        if b == 0 or a == 0:
            return 0.0
        return float(-math.log(a / b))
    except Exception:
        return 0.0


def compute_dfa_alpha1(rr: np.ndarray, scales: tuple[int, int] = (4, 16)) -> float:
    """Detrended Fluctuation Analysis, alpha1 sur échelles 4-16 beats."""
    n = len(rr)
    if n < scales[1] * 2:
        return 0.0
    # Profil intégré
    y = np.cumsum(rr - np.mean(rr))

    scale_list = list(range(scales[0], scales[1] + 1))
    fluct = []
    for s in scale_list:
        n_seg = n // s
        if n_seg < 2:
            fluct.append(np.nan)
            continue
        rms_total = []
        for i in range(n_seg):
            seg = y[i * s:(i + 1) * s]
            x = np.arange(s)
            coef = np.polyfit(x, seg, 1)
            trend = np.polyval(coef, x)
            rms_total.append(np.sqrt(np.mean((seg - trend) ** 2)))
        fluct.append(np.mean(rms_total))

    fluct = np.array(fluct, dtype=float)
    valid = ~np.isnan(fluct) & (fluct > 0)
    if valid.sum() < 3:
        return 0.0

    log_s = np.log10(np.array(scale_list)[valid])
    log_f = np.log10(fluct[valid])
    slope, _ = np.polyfit(log_s, log_f, 1)
    return float(slope)


def compute_stress_index(rr: np.ndarray) -> float:
    """Indice de stress de Baevsky : SI = AMo / (2 * Mo * MxDMn)

    Mo : mode du RR (s)
    AMo : % de RR dans le bin mode
    MxDMn : (max - min)(s)

    Largeur de bin = 7.8125 ms (= 1/128 Hz, résolution native Polar/Kubios).
    Avec des bins plus larges (50 ms), AMo est surestimé et SI gonflé.
    """
    if len(rr) < 30:
        return 0.0
    bin_width = 1000.0 / 128.0  # = 7.8125 ms, convention Kubios
    rr_min, rr_max = float(np.min(rr)), float(np.max(rr))
    mxdmn = rr_max - rr_min
    if mxdmn <= 0:
        return 0.0
    n_bins = max(1, int(np.ceil(mxdmn / bin_width)))
    hist, edges = np.histogram(rr, bins=n_bins, range=(rr_min, rr_min + n_bins * bin_width))
    if hist.sum() == 0:
        return 0.0
    mode_idx = int(np.argmax(hist))
    mo = (edges[mode_idx] + edges[mode_idx + 1]) / 2.0
    amo_pct = 100.0 * hist[mode_idx] / hist.sum()
    mo_s = mo / 1000.0
    mxdmn_s = mxdmn / 1000.0
    if mo_s <= 0 or mxdmn_s <= 0:
        return 0.0
    si = amo_pct / (2.0 * mo_s * mxdmn_s)
    return float(si)


# =============================================================================
# Indices synthétiques PNS / SNS
# =============================================================================

def compute_synthetic_indices(metrics: dict[str, float]) -> tuple[float, float]:
    """PNS et SNS index — convention Kubios (Tarvainen).

    PNS = moyenne z-score de : Mean RR, RMSSD, HF n.u. (signe +)
    SNS = moyenne z-score de : Mean HR, Stress Index, LF n.u. (signe +)

    Bornes : [-3, +3].

    Références normatives (population adulte, position couchée — Kubios) :
      Mean RR       : μ=926,  σ=90    ms
      RMSSD         : μ=42,   σ=16    ms (log)
      HF n.u.       : μ=44,   σ=18
      Mean HR       : μ=66,   σ=7     bpm
      Stress Index  : μ=8,    σ=3     (log)
      LF n.u.       : μ=55,   σ=18
    """
    def z(v: float, mu: float, sd: float) -> float:
        return (v - mu) / sd if sd > 0 else 0.0

    mean_rr = metrics.get("mean_rr", 0.0)
    rmssd = metrics.get("rmssd", 0.0)
    hf_nu = metrics.get("hf_nu", 0.0)
    mean_hr = metrics.get("mean_hr", 0.0)
    si = metrics.get("stress_index", 0.0)
    lf_nu = metrics.get("lf_nu", 0.0)

    # log-transform pour RMSSD et SI (distributions log-normales en population)
    log_rmssd = math.log(max(rmssd, 1.0))
    log_si = math.log(max(si, 0.1))
    log_rmssd_mu = math.log(42.0)
    log_rmssd_sd = 0.4
    log_si_mu = math.log(8.0)
    log_si_sd = 0.6

    pns = (z(mean_rr, 926, 90)
           + z(log_rmssd, log_rmssd_mu, log_rmssd_sd)
           + z(hf_nu, 44, 18)) / 3.0
    sns = (z(mean_hr, 66, 7)
           + z(log_si, log_si_mu, log_si_sd)
           + z(lf_nu, 55, 18)) / 3.0

    pns = float(max(-3.0, min(3.0, pns)))
    sns = float(max(-3.0, min(3.0, sns)))
    return pns, sns


# =============================================================================
# Assemblage : analyser un segment complet
# =============================================================================

def analyze_segment(rr: np.ndarray) -> SegmentMetrics:
    seg = SegmentMetrics()
    seg.n_beats = len(rr)
    seg.duration_s = float(np.sum(rr) / 1000.0) if len(rr) else 0.0
    if len(rr) < 30:
        return seg

    td = compute_time_domain(rr)
    fd = compute_frequency_domain(rr)
    pc = compute_poincare(rr)
    sampen = compute_sampen(rr)
    dfa = compute_dfa_alpha1(rr)
    si = compute_stress_index(rr)

    all_m = {**td, **fd, **pc, "sampen": sampen, "dfa_alpha1": dfa, "stress_index": si}
    pns, sns = compute_synthetic_indices(all_m)

    for k, v in all_m.items():
        if hasattr(seg, k):
            setattr(seg, k, v)
    seg.pns_index = pns
    seg.sns_index = sns
    return seg


# =============================================================================
# Classification Freville
# =============================================================================

def classify_fatigue(sitting: SegmentMetrics, standing: SegmentMetrics) -> dict[str, Any]:
    """Applique la grille Freville. Renvoie type, label, couleur, recommandations, alertes."""
    delta_hr = standing.mean_hr - sitting.mean_hr
    alerts: list[dict[str, str]] = []
    recommendations: list[str] = []

    fatigue_type = "normal"
    fatigue_label = "Bilan dans la norme"
    color = "green"

    # Hors norme = forme optimale
    if 10 <= delta_hr <= 25 and standing.lf_hf < 3.5 and sitting.hf > 3000:
        fatigue_type = "optimal"
        fatigue_label = "Forme optimale (hors norme positif)"
        color = "dark_green"
        recommendations = [
            "Profil exceptionnel : équilibre para/ortho excellent.",
            "Maintenir la charge actuelle, respecter la récupération.",
            "Inscrire ce profil comme référence personnelle.",
        ]
    # Type 1
    elif standing.lf_hf > 1.5 and (delta_hr > 25 or sitting.lf > 1500):
        fatigue_type = "type1"
        fatigue_label = "Type 1 — Hypertonie ortho (couché)"
        color = "orange"
        recommendations = [
            "Excès d'intensité récent — activer le parasympathique.",
            "24-48h de récupération active : sortie aérobie longue Z1-Z2.",
            "Cohérence cardiaque 3×5 min/jour, sommeil prioritaire.",
            "Réduire le volume orthosympathique (intensités, charges lourdes).",
        ]
    # Type 2
    elif sitting.hf < 800 and sitting.rmssd < 50 and delta_hr > 20:
        fatigue_type = "type2"
        fatigue_label = "Type 2 — Hypotonie para (couché)"
        color = "red"
        recommendations = [
            "Suite non traitée du Type 1 — activer para en urgence.",
            "Repos complet 48-72h, aucune séance qualité.",
            "Travail respiratoire long, sommeil >9h, alimentation anti-inflammatoire.",
            "Revoir un test après 72h avant toute reprise.",
        ]
    # Type 3
    elif sitting.hf > 8000 and sitting.lf_hf < 0.2 and sitting.mean_hr < 45:
        fatigue_type = "type3"
        fatigue_label = "Type 3 — Hypertonie para (couché)"
        color = "red"
        recommendations = [
            "Surentraînement parasympathique — activer l'orthosympathique.",
            "Réintroduire des stimulus courts et intenses (sprints courts, force).",
            "Éviter le volume aérobie supplémentaire.",
            "Hydratation, exposition lumière, café modéré possible.",
        ]
    # Type 4
    elif standing.lf < 800 and delta_hr > 30 and standing.sns_index < 0:
        fatigue_type = "type4"
        fatigue_label = "Type 4 — Hypotonie ortho (debout)"
        color = "red"
        recommendations = [
            "Épuisement profond — activer l'orthosympathique.",
            "Hydratation et électrolytes prioritaires.",
            "Repos passif total 3-5 jours, pas d'entraînement.",
            "Bilan biologique recommandé (ferritine, vit D, cortisol).",
        ]
    # Type 5
    elif standing.hf > standing.lf and standing.lf_hf > 2.5 and sitting.mean_hr < 45:
        fatigue_type = "type5"
        fatigue_label = "Type 5 — Hypertonie para (debout)"
        color = "red"
        recommendations = [
            "Épuisement profond avec dominance vagale debout — activer ortho.",
            "Reprise progressive avec stimulus courts.",
            "Vérifier qualité du sommeil, charge cumulée.",
        ]
    else:
        recommendations = [
            "Profil dans la norme — pas de signal d'alarme.",
            "Poursuivre la planification prévue.",
        ]

    # Alertes additionnelles
    if sitting.sampen < 0.5 and sitting.sampen > 0:
        alerts.append({
            "level": "red",
            "label": "SNA rigide — SNC surchargé",
            "detail": f"SampEn couché = {sitting.sampen:.2f} (< 0.5)",
        })
    if sitting.vlf > 0 and standing.vlf / sitting.vlf > 2.0:
        alerts.append({
            "level": "orange",
            "label": "VLF élevé — surveiller hydratation",
            "detail": f"VLF debout/couché = {standing.vlf / sitting.vlf:.1f}",
        })

    # DFA alpha 1
    dfa = sitting.dfa_alpha1
    if dfa < 0.75:
        dfa_badge = {"level": "green", "label": "Intensité sous SV1", "value": f"{dfa:.2f}"}
    elif dfa <= 1.0:
        dfa_badge = {"level": "green_light", "label": "Proche SV1", "value": f"{dfa:.2f}"}
    elif dfa > 1.0:
        dfa_badge = {"level": "orange", "label": "Au-dessus SV1 — surveiller charge", "value": f"{dfa:.2f}"}
    else:
        dfa_badge = {"level": "green", "label": "DFA dans la norme", "value": f"{dfa:.2f}"}

    return {
        "fatigue_type": fatigue_type,
        "fatigue_label": fatigue_label,
        "fatigue_color": color,
        "delta_hr": delta_hr,
        "recommendations": recommendations,
        "alerts": alerts,
        "dfa_badge": dfa_badge,
    }


# =============================================================================
# Pipeline complet
# =============================================================================

def analyze_rr_file(
    rr: np.ndarray,
    transition_idx: int | None = None,
    clean: bool = True,
    diff_max_ms: float = DIFF_MAX_MS,
    malik_threshold: float = 0.30,
) -> AnalysisResult:
    """Pipeline complet pour un fichier RR brut.

    Par défaut (clean=True) : filtre physiologique en 3 couches.
      - Bornes RR absolues (300-2000 ms = HR entre 30 et 200 bpm)
      - Diff RR-RR absolue (|Δ| > 400 ms par défaut → artefact évident)
      - Malik 30 % en complément (variations relatives suspectes)

    Ce filtre est plus précis que Malik 20 % seul : il ne s'écrase pas sur
    les vraies grandes variations physiologiques des sportifs très HRV
    (sinus arythmie respiratoire pouvant donner des diffs > 200 ms en élite),
    mais élimine les battements manqués / ectopiques (diffs > 400 ms).
    """
    n_artifacts = 0
    if clean:
        rr, n_artifacts = clean_rr_physiological(
            rr, diff_max_ms=diff_max_ms, malik_threshold=malik_threshold
        )

    if transition_idx is None:
        transition_idx = detect_transition(rr)

    sitting_rr, standing_rr, transition_time_s = split_segments(rr, transition_idx)

    sit_metrics = analyze_segment(sitting_rr)
    sta_metrics = analyze_segment(standing_rr)

    cls = classify_fatigue(sit_metrics, sta_metrics)

    return AnalysisResult(
        sitting=sit_metrics,
        standing=sta_metrics,
        delta_hr=cls["delta_hr"],
        transition_index=transition_idx,
        transition_time_s=transition_time_s,
        total_duration_s=float(np.sum(rr) / 1000.0),
        n_beats_total=len(rr),
        n_artifacts_removed=n_artifacts,
        fatigue_type=cls["fatigue_type"],
        fatigue_label=cls["fatigue_label"],
        fatigue_color=cls["fatigue_color"],
        recommendations=cls["recommendations"],
        alerts=cls["alerts"],
        dfa_badge=cls["dfa_badge"],
    )


def analyze_kubios_csv(content: str) -> AnalysisResult | None:
    """Construit un AnalysisResult depuis un Kubios CSV (sans recalcul)."""
    parsed = parse_kubios_csv(content)
    if not parsed:
        return None

    def build_segment(d: dict[str, float]) -> SegmentMetrics:
        seg = SegmentMetrics()
        # Mapping des noms Kubios usuels (insensible à la casse)
        kl = {k.lower(): v for k, v in d.items()}

        def get(*names: str) -> float:
            for n in names:
                if n.lower() in kl:
                    return float(kl[n.lower()])
            return 0.0

        seg.mean_rr = get("Mean RR (ms)", "Mean RR")
        seg.mean_hr = get("Mean HR (beats/min)", "Mean HR", "HR mean")
        seg.min_hr = get("Min HR (beats/min)", "Min HR")
        seg.max_hr = get("Max HR (beats/min)", "Max HR")
        seg.sdnn = get("SDNN (ms)", "SDNN")
        seg.rmssd = get("RMSSD (ms)", "RMSSD")
        seg.pnn50 = get("pNN50 (%)", "pNN50")
        seg.vlf = get("VLF power (ms^2)", "VLF (ms^2)", "VLF")
        seg.lf = get("LF power (ms^2)", "LF (ms^2)", "LF")
        seg.hf = get("HF power (ms^2)", "HF (ms^2)", "HF")
        seg.total_power = get("Total power (ms^2)", "Total Power") or (seg.vlf + seg.lf + seg.hf)
        seg.lf_hf = get("LF/HF ratio", "LF/HF") or (seg.lf / seg.hf if seg.hf > 0 else 0)
        seg.lf_pct = get("LF (%)", "LF%")
        seg.hf_pct = get("HF (%)", "HF%")
        seg.vlf_pct = get("VLF (%)", "VLF%")
        seg.lf_nu = get("LF (n.u.)", "LF nu")
        seg.hf_nu = get("HF (n.u.)", "HF nu")
        seg.sd1 = get("SD1 (ms)", "SD1")
        seg.sd2 = get("SD2 (ms)", "SD2")
        seg.sd2_sd1 = get("SD2/SD1") or (seg.sd2 / seg.sd1 if seg.sd1 > 0 else 0)
        seg.sampen = get("Sample entropy", "SampEn")
        seg.dfa_alpha1 = get("alpha 1", "DFA alpha1", "alpha1")
        seg.stress_index = get("Stress index", "Baevsky's stress index")
        # Recompute PNS/SNS si non fournis
        pns, sns = compute_synthetic_indices(asdict(seg))
        seg.pns_index = get("PNS index") or pns
        seg.sns_index = get("SNS index") or sns
        return seg

    sit = build_segment(parsed.get("sitting", {}))
    sta = build_segment(parsed.get("standing", {}))
    cls = classify_fatigue(sit, sta)

    return AnalysisResult(
        sitting=sit,
        standing=sta,
        delta_hr=cls["delta_hr"],
        transition_index=0,
        transition_time_s=300.0,
        total_duration_s=sit.duration_s + sta.duration_s,
        n_beats_total=sit.n_beats + sta.n_beats,
        n_artifacts_removed=0,
        fatigue_type=cls["fatigue_type"],
        fatigue_label=cls["fatigue_label"],
        fatigue_color=cls["fatigue_color"],
        recommendations=cls["recommendations"],
        alerts=cls["alerts"],
        dfa_badge=cls["dfa_badge"],
    )


# =============================================================================
# Codes couleur pour les marqueurs (UI)
# =============================================================================

def marker_color(name: str, value: float, position: str = "sitting") -> str:
    """Retourne 'green', 'orange' ou 'red' selon la norme du marqueur."""
    if position == "sitting":
        if name == "rmssd":
            if value < 30: return "red"
            if value < 60: return "orange"
            return "green"
        if name == "hf":
            if value < 500: return "red"
            if value < 2000: return "orange"
            return "green"
        if name == "sampen":
            if value < 0.5: return "red"
            if value < 1.0: return "orange"
            return "green"
        if name == "dfa_alpha1":
            if value > 1.2: return "orange"
            return "green"
    if position == "standing":
        if name == "lf_hf":
            if value > 8: return "red"
            if value > 3.5: return "orange"
            return "green"
    if name == "delta_hr":
        if value > 35: return "red"
        if value > 25: return "orange"
        if value < 10: return "orange"
        return "green"
    return "green"


__all__ = [
    "SegmentMetrics",
    "AnalysisResult",
    "extract_date_from_filename",
    "parse_rr_txt",
    "parse_kubios_csv",
    "analyze_rr_file",
    "analyze_kubios_csv",
    "detect_transition",
    "marker_color",
]

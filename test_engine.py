"""Test rapide du moteur HRV avec un signal synthétique."""
import numpy as np
from hrv_engine import (
    analyze_rr_file, parse_rr_txt, extract_date_from_filename,
    detect_transition, clean_rr_malik, clean_rr_physiological,
    compute_time_domain, compute_frequency_domain, compute_poincare,
    compute_sampen, compute_dfa_alpha1, compute_stress_index,
)


def generate_test_signal():
    """Génère un signal synthétique avec transition assis (~65 bpm) -> debout (~85 bpm)."""
    rng = np.random.default_rng(42)
    # Assis : 5 min @ ~65 bpm => RR ~ 920 ms, n_beats ~ 326
    n_sit = 326
    rr_sit_base = 920
    sit = rr_sit_base + 30 * np.sin(np.linspace(0, 20, n_sit)) + rng.normal(0, 25, n_sit)
    # Debout : 5 min @ ~85 bpm => RR ~ 705 ms, n_beats ~ 426
    n_std = 426
    rr_std_base = 705
    std = rr_std_base + 15 * np.sin(np.linspace(0, 20, n_std)) + rng.normal(0, 20, n_std)
    return np.concatenate([sit, std])


def test_filename_date():
    d = extract_date_from_filename("2026-05-04_07-52-32.txt")
    assert d is not None and d.year == 2026 and d.month == 5 and d.day == 4
    assert d.hour == 7 and d.minute == 52
    d2 = extract_date_from_filename("athlete_data_2026-01-15.txt")
    assert d2 is not None and d2.day == 15
    d3 = extract_date_from_filename("nodate.txt")
    assert d3 is None
    print("[OK] extract_date_from_filename")


def test_parse_rr_txt():
    content = "1143\n966\n895\n900\n910\n"
    rr = parse_rr_txt(content)
    assert len(rr) == 5
    assert rr[0] == 1143.0
    # Avec virgules
    rr2 = parse_rr_txt("1143, 966, 895")
    assert len(rr2) == 3
    print("[OK] parse_rr_txt")


def test_clean_malik():
    rr = np.array([900, 920, 910, 1500, 905, 915], dtype=float)
    cleaned, n = clean_rr_malik(rr)
    assert n == 1
    assert cleaned[3] < 1500
    print("[OK] clean_rr_malik")


def test_clean_physiological():
    # Cas 1 : battement manqué isolé (1380 = 700+680 mergé), suite normale
    rr = np.array([720, 720, 720, 1380, 720, 720, 720], dtype=float)
    cleaned, n = clean_rr_physiological(rr)
    assert n == 1, f"Expected 1 artifact, got {n}"
    assert cleaned[3] < 1000, f"1380 should be corrected, got {cleaned[3]}"
    # Cas 2 : ectopique (long RR puis très court RR)
    rr_ecto = np.array([720, 720, 1200, 240, 720, 720], dtype=float)
    cleaned_ecto, n_ecto = clean_rr_physiological(rr_ecto)
    assert n_ecto == 2, f"Expected 2 artifacts (1200 + 240), got {n_ecto}"
    # Cas 3 : athlète HRV élevée — diffs jusqu'à 200 ms doivent être préservés
    rr2 = np.array([900, 1080, 920, 1100, 950, 1090, 940, 1060], dtype=float)
    cleaned2, n2 = clean_rr_physiological(rr2)
    assert n2 == 0, f"Athlete HRV should not be filtered, got {n2} artifacts"
    # Cas 4 : RR hors bornes physiologiques
    rr3 = np.array([900, 920, 200, 940, 2500, 910], dtype=float)
    cleaned3, n3 = clean_rr_physiological(rr3)
    assert n3 == 2, f"Expected 2 out-of-bound, got {n3}"
    print(f"[OK] clean_rr_physiological (missed={n}, ectopic={n_ecto}, athlete preserved, bounds={n3})")


def test_time_domain():
    rr = np.array([900, 920, 910, 940, 905, 915, 950], dtype=float)
    m = compute_time_domain(rr)
    assert m["mean_rr"] > 900 and m["mean_rr"] < 950
    assert 60 < m["mean_hr"] < 70
    assert m["rmssd"] > 0
    print(f"[OK] time_domain (mean_hr={m['mean_hr']:.1f}, rmssd={m['rmssd']:.1f})")


def test_frequency_domain():
    rng = np.random.default_rng(0)
    rr = 900 + 30 * np.sin(np.linspace(0, 40, 400)) + rng.normal(0, 10, 400)
    f = compute_frequency_domain(rr)
    assert f["total_power"] > 0
    assert f["lf"] > 0 or f["hf"] > 0
    print(f"[OK] frequency_domain (LF={f['lf']:.0f}, HF={f['hf']:.0f}, LF/HF={f['lf_hf']:.2f})")


def test_poincare():
    rr = 900 + np.random.randn(200) * 30
    p = compute_poincare(rr)
    assert p["sd1"] > 0 and p["sd2"] > 0
    print(f"[OK] poincare (SD1={p['sd1']:.1f}, SD2={p['sd2']:.1f})")


def test_sampen_dfa():
    rng = np.random.default_rng(1)
    rr = 900 + rng.normal(0, 30, 300)
    sampen = compute_sampen(rr)
    dfa = compute_dfa_alpha1(rr)
    assert sampen >= 0
    assert dfa >= 0
    print(f"[OK] sampen={sampen:.2f}, dfa_alpha1={dfa:.2f}")


def test_stress_index():
    rng = np.random.default_rng(2)
    rr = 900 + rng.normal(0, 30, 300)
    si = compute_stress_index(rr)
    assert si >= 0
    print(f"[OK] stress_index={si:.1f}")


def test_detect_transition():
    rr = generate_test_signal()
    idx = detect_transition(rr)
    # La transition réelle est à n_sit=326
    print(f"[OK] detect_transition: {idx} (attendu ~326)")
    assert abs(idx - 326) < 50


def test_full_pipeline():
    rr = generate_test_signal()
    result = analyze_rr_file(rr)
    assert result.sitting.n_beats > 0
    assert result.standing.n_beats > 0
    assert result.sitting.mean_hr < result.standing.mean_hr
    print(f"[OK] full_pipeline:")
    print(f"     Assis: HR={result.sitting.mean_hr:.1f}bpm, RMSSD={result.sitting.rmssd:.1f}, LF/HF={result.sitting.lf_hf:.2f}")
    print(f"     Debout: HR={result.standing.mean_hr:.1f}bpm, RMSSD={result.standing.rmssd:.1f}, LF/HF={result.standing.lf_hf:.2f}")
    print(f"     Delta HR={result.delta_hr:.1f}bpm, Type={result.fatigue_type}, Label={result.fatigue_label}")
    print(f"     PNS={result.sitting.pns_index:.2f}, SNS={result.sitting.sns_index:.2f}")


if __name__ == "__main__":
    test_filename_date()
    test_parse_rr_txt()
    test_clean_malik()
    test_clean_physiological()
    test_time_domain()
    test_frequency_domain()
    test_poincare()
    test_sampen_dfa()
    test_stress_index()
    test_detect_transition()
    test_full_pipeline()
    print("\n*** Tous les tests passent ***")

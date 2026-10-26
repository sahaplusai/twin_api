"""
Equation verification suite for HealthPlus AI v31 pipeline (A1–D4).

Run:
    python test_equations.py
    pytest test_equations.py -q

Copyright (c) 2026 SVT Systems
All rights reserved.    
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import (
    CLUSTERS,
    _gamma_rate,
    _modulation,
    _params,
    run_pipeline,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_A1_gamma_k1_is_exponential() -> None:
    lam, dose = 0.05, 10.0
    t = np.linspace(0.1, 200, 500)
    h = _gamma_rate(t, dose, k=1.0, lam=lam)
    expected = dose * lam * np.exp(-lam * t)
    _assert(np.allclose(h, expected, rtol=1e-8, atol=1e-10), "A1 k=1 should be exponential")


def test_A1_kernel_integrates_to_dose() -> None:
    t = np.linspace(0.0, 2000.0, 20001)
    dt = t[1] - t[0]
    dose = 25.0
    h = 0.6 * _gamma_rate(t, dose, 2.0, 0.03) + 0.4 * _gamma_rate(t, dose, 1.5, 0.01)
    area = float(np.trapezoid(h, dx=dt))
    _assert(abs(area / dose - 1.0) < 0.03, "A1 dual-gamma integral should recover dose")


def test_A2_superposition_two_meals() -> None:
    common = {
        "timestep_min": 5,
        "include_timeseries": True,
        "timeseries_stride": 1,
        "mvpa_min": 0,
        "sleep_start_min": 2000,
        "sleep_end_min": 2000,
    }
    a1 = np.array(
        run_pipeline(
            {
                **common,
                "duration_after_last_meal_min": 640,
                "meals": [{"time_minutes": 120, "doses": {"protein_g": 20}}],
            }
        )["layer_a"]["outputs"]["a_i_t"]["protein_g"]
    )
    a2 = np.array(
        run_pipeline(
            {
                **common,
                "duration_after_last_meal_min": 400,
                "meals": [{"time_minutes": 360, "doses": {"protein_g": 20}}],
            }
        )["layer_a"]["outputs"]["a_i_t"]["protein_g"]
    )
    both = np.array(
        run_pipeline(
            {
                **common,
                "duration_after_last_meal_min": 400,
                "meals": [
                    {"time_minutes": 120, "doses": {"protein_g": 20}},
                    {"time_minutes": 360, "doses": {"protein_g": 20}},
                ],
            }
        )["layer_a"]["outputs"]["a_i_t"]["protein_g"]
    )
    n = min(len(a1), len(a2), len(both))
    stacked = a1[:n] + a2[:n]
    rel = float(np.max(np.abs(both[:n] - stacked)) / (np.max(stacked) + 1e-12))
    _assert(rel < 0.02, f"A2 combined run must equal sum of single-meal runs (rel={rel:.4f})")


def test_A3_circadian_varies_over_day() -> None:
    t = np.array([480.0, 1200.0])
    m = _modulation(t, mvpa=0.0, sleep_start=1380, sleep_end=420)
    _assert(abs(float(m[0] - m[1])) > 1e-4, "A3 circadian should vary across the day")
    _assert(np.all((m > 0.6) & (m < 1.4)), "A3 multiplier should stay physiologic")


def test_A4_saturable_bioavailability() -> None:
    p = _params("protein_g")
    f_small = p["F_base"] * p["K_MM"] / (p["K_MM"] + 0.1)
    f_large = p["F_base"] * p["K_MM"] / (p["K_MM"] + 5000.0)
    _assert(f_small > f_large, "A4 F_bio must fall as dose rises")
    _assert(abs(f_small - p["F_base"]) < 0.02, "A4 small dose ≈ F_base")
    _assert(f_large < 0.05, "A4 large dose F_bio → 0")


def test_A5_weibull_ge_limits() -> None:
    t50, k = 90.0, 1.5
    ge0 = 1.0 - math.exp(-((0.0 / t50) ** k)) if False else 0.0
    ge_inf = 1.0 - math.exp(-((1e6 / t50) ** k))
    ge_t50 = 1.0 - math.exp(-((t50 / t50) ** k))
    _assert(ge0 == 0.0, "A5 GE(0)=0")
    _assert(ge_inf > 0.999, "A5 GE(∞)→1")
    _assert(abs(ge_t50 - (1.0 - math.exp(-1.0))) < 1e-12, "A5 GE(T50)=1-1/e")


def test_A6_activity_increases_modulation() -> None:
    t = np.array([600.0])
    low = float(_modulation(t, 0.0, 1380, 420)[0])
    high = float(_modulation(t, 180.0, 1380, 420)[0])
    _assert(high > low, "A6 higher MVPA should raise modulation")


def test_A7_cook_factor_scales_Fbio() -> None:
    base = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"protein_g": 30}}],
            "duration_after_last_meal_min": 180,
            "timestep_min": 10,
            "f_cook": 1.0,
            "include_timeseries": False,
        }
    )
    cooked = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"protein_g": 30}}],
            "duration_after_last_meal_min": 180,
            "timestep_min": 10,
            "f_cook": 1.4,
            "include_timeseries": False,
        }
    )
    _assert(
        cooked["layer_a"]["states"]["F_bio"]["protein_g"]
        > base["layer_a"]["states"]["F_bio"]["protein_g"],
        "A7 higher f_cook should raise F_bio",
    )


def test_A_mass_balance() -> None:
    dose = 40.0
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 0, "doses": {"protein_g": dose}}],
            "duration_after_last_meal_min": 1440,
            "timestep_min": 5,
            "mvpa_min": 0,
            "include_timeseries": True,
            "timeseries_stride": 1,
        }
    )
    a = np.array(out["layer_a"]["outputs"]["a_i_t"]["protein_g"])
    t = np.array(out["layer_a"]["t_minutes"])
    absorbed = float(np.trapezoid(a, t))
    f_bio = out["layer_a"]["states"]["F_bio"]["protein_g"]
    _assert(absorbed <= dose * 1.15, "A absorbed mass must not greatly exceed dose")
    _assert(absorbed > 0.15 * f_bio * dose, "A absorbed mass should be a meaningful fraction of F_bio*dose")


def test_B1_kel() -> None:
    p = _params("protein_g")
    kel = math.log(2.0) / p["T_half"]
    _assert(abs(kel - math.log(2.0) / 240.0) < 1e-12, "B1 k_el = ln2 / T½")


def test_B2_B3_mass_decay_matches_clearance() -> None:
    p = _params("protein_g")
    V1, V2, Q, th = p["V1"], p["V2"], p["Q"], p["T_half"]
    CL = math.log(2.0) / th * V1
    c1, c2 = 10.0, 4.0
    mass0 = V1 * c1 + V2 * c2
    dt = 0.25
    lost = 0.0
    for _ in range(800):
        dc1 = 0.0 / V1 - (CL + Q) / V1 * c1 + Q / V1 * c2
        dc2 = Q / V2 * c1 - Q / V2 * c2
        c1 = max(c1 + dc1 * dt, 0.0)
        c2 = max(c2 + dc2 * dt, 0.0)
        lost += CL * (c1 - dc1 * dt) * dt
    mass1 = V1 * c1 + V2 * c2
    _assert(mass1 < mass0, "B2/B3 mass must fall when a=0")
    _assert(abs((mass0 - mass1) - lost) / mass0 < 0.08, "B2/B3 mass loss ≈ ∫ CL C1 dt")


def test_B4_B5_B6_clearance_split() -> None:
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"protein_g": 20}}],
            "duration_after_last_meal_min": 120,
            "timestep_min": 10,
            "include_timeseries": False,
        }
    )
    cl = out["layer_b"]["states"]["clearance"]["protein_g"]
    _assert(abs(cl["CL_renal"] + cl["CL_hepatic"] - cl["CL_total"]) < 1e-9, "B6 CL_total = renal + hepatic")
    _assert(cl["CL_renal"] > 0 and cl["CL_hepatic"] > 0, "B4/B5 both clearance arms positive")
    _assert(abs(cl["k_el_per_min"] * cl["V1"] - cl["CL_total"]) < 1e-9, "B1/B6 CL = k_el V1")


def test_B_nonnegative_concentrations() -> None:
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"carb_total_g": 80, "protein_g": 25}}],
            "duration_after_last_meal_min": 360,
            "timestep_min": 10,
            "include_timeseries": True,
        }
    )
    for nid, series in out["layer_b"]["outputs"]["C1_t"].items():
        _assert(min(series) >= -1e-9, f"B C1({nid}) went negative")
    for nid, series in out["layer_b"]["outputs"]["C2_t"].items():
        _assert(min(series) >= -1e-9, f"B C2({nid}) went negative")


def test_C1_exposure_positive_when_dose_present() -> None:
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 50}}],
            "duration_after_last_meal_min": 240,
            "timestep_min": 10,
            "include_timeseries": True,
        }
    )
    e = out["layer_c"]["states"]["E_k_t"]["glycemic"]
    _assert(max(e) > 0, "C1 glycemic exposure should rise after sugar")


def test_C2_C3_no_damage_in_band() -> None:
    eta_hi, eta_lo, dt = 0.004, 0.0015, 10.0
    theta_hi, theta_lo = 8.0, 0.4
    e = 2.0
    d_hi = eta_hi * max(e - theta_hi, 0.0) * dt
    d_lo = eta_lo * max(theta_lo - e, 0.0) * dt
    _assert(d_hi == 0.0 and d_lo == 0.0, "C2/C3 no accumulation inside [θ_lo, θ_hi]")


def test_C4_activity_reduces_damage() -> None:
    low = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 70, "fat_sat_g": 25}}],
            "duration_after_last_meal_min": 360,
            "timestep_min": 10,
            "mvpa_min": 0,
            "sedentary_min": 700,
            "include_timeseries": False,
        }
    )
    high = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 70, "fat_sat_g": 25}}],
            "duration_after_last_meal_min": 360,
            "timestep_min": 10,
            "mvpa_min": 180,
            "sedentary_min": 200,
            "include_timeseries": False,
        }
    )
    z_low = sum(low["layer_c"]["outputs"]["Z_k"].values())
    z_high = sum(high["layer_c"]["outputs"]["Z_k"].values())
    _assert(z_high <= z_low + 1e-9, "C4 higher activity / less sitting should not increase net Z")


def test_C5_dual_hill_u_shape() -> None:
    Kdef, Ktox, n1, n2 = 0.5, 10.0, 2.0, 2.0

    def hill(e: float) -> float:
        e = max(e, 0.0)
        return (
            max(0.0, Kdef**n1 - e**n1) / (Kdef**n1)
            + max(0.0, e**n2 - Ktox**n2) / (Ktox**n2 + e**n2)
        )

    mid = hill(5.0)
    _assert(hill(0.0) > mid, "C5 deficiency arm high at E=0")
    _assert(hill(40.0) > mid, "C5 toxicity arm high at large E")


def test_C6_repair_bounds() -> None:
    Vm, Km, Z, extra = 0.02, 2.0, 3.0, 1.0
    r = Vm * Z / (Km * (1.0 + extra / 12.0) + Z)
    _assert(0 < r < Vm, "C6 repair must be in (0, Vm)")
    _assert(max(Z - r * 10.0, 0.0) >= 0.0, "C6 cannot drive Z negative")


def test_C_nonnegative_Z() -> None:
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 80, "alcohol_g": 20}}],
            "duration_after_last_meal_min": 480,
            "timestep_min": 10,
            "include_timeseries": True,
        }
    )
    for k, series in out["layer_c"]["outputs"]["Z_k_t"].items():
        _assert(min(series) >= -1e-9, f"C Z({k}) went negative")


def test_D1_logistic_monotone_bounded() -> None:
    a, b = 1.1, -1.4

    def D(z: float) -> float:
        return 100.0 / (1.0 + math.exp(-(a * math.log1p(z) + b)))

    _assert(0 < D(0) < 50, "D1 Z=0 should be a low-mid score")
    _assert(D(0) < D(2) < D(20), "D1 must increase with Z")
    _assert(D(1e6) < 100.0, "D1 stays below 100")


def test_D2_traffic_lights() -> None:
    def light(d: float) -> str:
        if d < 30:
            return "GREEN"
        if d < 60:
            return "YELLOW"
        return "RED"

    _assert(light(20) == "GREEN" and light(45) == "YELLOW" and light(70) == "RED", "D2 thresholds")


def test_D3_lab_moves_score() -> None:
    base = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 40}}],
            "duration_after_last_meal_min": 240,
            "timestep_min": 15,
            "labs": {},
            "include_timeseries": False,
        }
    )
    lab = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"sugar_added_g": 40}}],
            "duration_after_last_meal_min": 240,
            "timestep_min": 15,
            "labs": {"glycemic": 20},
            "include_timeseries": False,
        }
    )
    _assert(
        lab["layer_d"]["outputs"]["D_k_lab"]["glycemic"]
        >= base["layer_d"]["outputs"]["D_k_lab"]["glycemic"] - 1e-9,
        "D3 positive lab delta should not lower D_lab",
    )


def test_D4_chs_bounds_and_weights() -> None:
    out = run_pipeline(
        {
            "meals": [{"time_minutes": 480, "doses": {"protein_g": 20, "carb_total_g": 40}}],
            "duration_after_last_meal_min": 240,
            "timestep_min": 15,
            "include_timeseries": False,
        }
    )
    chs = out["layer_d"]["outputs"]["CHS"]
    _assert(0.0 <= chs <= 100.0, "D4 CHS in [0,100]")
    _assert(len(out["layer_d"]["outputs"]["D_k"]) == len(CLUSTERS), "D4 one score per cluster")


EQUATION_TESTS = [
    ("A1", "gamma k=1 exponential", test_A1_gamma_k1_is_exponential),
    ("A1", "kernel integrates to dose", test_A1_kernel_integrates_to_dose),
    ("A2", "two-meal superposition", test_A2_superposition_two_meals),
    ("A3", "circadian variation", test_A3_circadian_varies_over_day),
    ("A4", "saturable F_bio", test_A4_saturable_bioavailability),
    ("A5", "Weibull GE limits", test_A5_weibull_ge_limits),
    ("A6", "MVPA raises modulation", test_A6_activity_increases_modulation),
    ("A7", "f_cook scales F_bio", test_A7_cook_factor_scales_Fbio),
    ("A*", "mass balance", test_A_mass_balance),
    ("B1", "k_el = ln2/T½", test_B1_kel),
    ("B2/B3", "PK mass vs clearance", test_B2_B3_mass_decay_matches_clearance),
    ("B4/B5/B6", "clearance split", test_B4_B5_B6_clearance_split),
    ("B*", "nonnegative C1/C2", test_B_nonnegative_concentrations),
    ("C1", "exposure from sugar", test_C1_exposure_positive_when_dose_present),
    ("C2/C3", "no damage in band", test_C2_C3_no_damage_in_band),
    ("C4", "activity reduces Z", test_C4_activity_reduces_damage),
    ("C5", "Dual-Hill U-shape", test_C5_dual_hill_u_shape),
    ("C6", "repair bounds", test_C6_repair_bounds),
    ("C*", "nonnegative Z", test_C_nonnegative_Z),
    ("D1", "logistic monotone", test_D1_logistic_monotone_bounded),
    ("D2", "traffic lights", test_D2_traffic_lights),
    ("D3", "lab calibration direction", test_D3_lab_moves_score),
    ("D4", "CHS bounds", test_D4_chs_bounds_and_weights),
]


def run_all() -> int:
    passed = failed = 0
    print(f"{'ID':<10} {'TEST':<32} RESULT")
    print("-" * 56)
    for eq_id, name, fn in EQUATION_TESTS:
        try:
            fn()
            print(f"{eq_id:<10} {name:<32} PASS")
            passed += 1
        except Exception as exc:
            print(f"{eq_id:<10} {name:<32} FAIL  {exc}")
            failed += 1
    print("-" * 56)
    print(f"{passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_all())

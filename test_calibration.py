"""
Calibrate / compare pipeline defaults against HealthAI_v31.xlsx
(P1 Nutrients 80 registry).

Run:
    python test_calibration.py

Copyright (c) 2026 SVT Systems
All rights reserved.    
"""

from __future__ import annotations

import csv
import math
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import CLUSTERS, NUTRIENT_IDS, _params

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

XLSX_CANDIDATES = [
    Path("/home/medmodel/HealthAI_v31.xlsx"),
    Path(__file__).resolve().parents[2] / "attachments" / "HealthAI_v31.xlsx",
]

EXCEL_CLUSTERS = [
    "membrane",
    "glucose",
    "protein",
    "electrolyte",
    "inflammation",
    "oxidative",
    "methylation",
    "gut",
    "neuro",
    "bone",
    "thyroid",
    "cardio",
]

PIPELINE_TO_EXCEL_CLUSTER = {
    "energy_metabolic": "glucose",
    "glycemic": "glucose",
    "lipid": "membrane",
    "protein_amino": "protein",
    "oxidative_stress": "oxidative",
    "inflammation": "inflammation",
    "bone_mineral": "bone",
    "hematopoietic": None,
    "cardiovascular": "cardio",
    "neuro_cognitive": "neuro",
    "gi_gut": "gut",
    "endocrine": "thyroid",
}


def _find_xlsx() -> Path:
    for p in XLSX_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("HealthAI_v31.xlsx not found")


def _colrow(ref: str) -> tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col, row = m.group(1), int(m.group(2))
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n, row


def _load_workbook(path: Path):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    ss = []
    for si in root.findall("m:si", NS):
        ss.append("".join((t.text or "") for t in si.findall(".//m:t", NS)))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {rel.attrib.get("Id"): rel.attrib.get("Target") for rel in rels}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    name_to_path = {}
    for s in wb.findall("m:sheets/m:sheet", NS):
        rid = s.attrib.get(f"{REL_NS}id")
        target = rid_to_target[rid]
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        name_to_path[s.attrib["name"]] = target
    return z, ss, name_to_path


def _read_sheet(z, ss, path: str) -> dict[int, dict[int, str]]:
    root = ET.fromstring(z.read(path))
    grid: dict[int, dict[int, str]] = defaultdict(dict)
    for c in root.findall(".//m:c", NS):
        ref = c.attrib.get("r")
        if not ref:
            continue
        col, row = _colrow(ref)
        v = c.find("m:v", NS)
        if v is None or v.text is None:
            continue
        val = v.text
        if c.attrib.get("t") == "s":
            val = ss[int(val)]
        grid[row][col] = val
    return grid


def load_nutrient_registry(xlsx: Path) -> dict[str, dict[str, float]]:
    z, ss, names = _load_workbook(xlsx)
    grid = _read_sheet(z, ss, names["P1 Nutrients 80"])
    header_row = 4
    headers = {}
    for col, val in grid[header_row].items():
        key = re.sub(r"\s+", " ", str(val)).strip().lower()
        headers[col] = key

    def col_of(*needles: str) -> int | None:
        for col, h in headers.items():
            if all(n in h for n in needles):
                return col
        return None

    cols = {
        "id": col_of("id") or 2,
        "k1": col_of("γ_k") or col_of("shape") or 6,
        "t50": col_of("γ_θ") or 7,
        "lam": col_of("λ") or 8,
        "w_fast": col_of("w_fast") or 11,
        "t_half_d": col_of("t½_fast") or col_of("t1/2_fast") or 12,
        "vf": col_of("v_f") or 14,
    }
    out = {}
    for r in range(5, 90):
        nid = str(grid[r].get(cols["id"], "")).strip()
        if not nid:
            continue
        rec = {}
        for k, c in cols.items():
            if k == "id":
                continue
            raw = str(grid[r].get(c, "")).strip()
            try:
                rec[k] = float(raw)
            except ValueError:
                rec[k] = math.nan
        out[nid] = rec
    return out


def rel_err(pipe: float, ref: float) -> float:
    if not math.isfinite(ref) or abs(ref) < 1e-15:
        return math.nan
    return abs(pipe - ref) / abs(ref)


def run_report() -> int:
    xlsx = _find_xlsx()
    registry = load_nutrient_registry(xlsx)
    rows = []
    covered = 0
    within_20 = within_50 = 0
    n_cmp = 0

    print(f"Reference: {xlsx}")
    print(f"Excel nutrients parsed: {len(registry)}")
    print(f"Pipeline nutrient IDs:  {len(NUTRIENT_IDS)}")
    missing = [n for n in NUTRIENT_IDS if n not in registry]
    extra = [n for n in registry if n not in NUTRIENT_IDS]
    print(f"IDs in pipeline but not Excel: {missing[:12]}{'...' if len(missing)>12 else ''} ({len(missing)})")
    print(f"IDs in Excel but not pipeline: {extra[:12]}{'...' if len(extra)>12 else ''} ({len(extra)})")
    print()
    print(f"{'NUTRIENT':<22} {'PARAM':<10} {'PIPELINE':>10} {'EXCEL':>10} {'REL_ERR':>8} FLAG")
    print("-" * 72)

    for nid in NUTRIENT_IDS:
        ref = registry.get(nid)
        if not ref:
            continue
        covered += 1
        p = _params(nid)
        checks = [
            ("k1", p["k1"], ref["k1"]),
            ("lambda1", p["lambda1"], ref["lam"]),
            ("w_fast", p["w_fast"], ref["w_fast"]),
            ("T50_GE", p["T50_GE"], ref["t50"]),
            ("T_half_min", p["T_half"], ref["t_half_d"] * 1440.0 if math.isfinite(ref["t_half_d"]) else math.nan),
        ]
        for name, pipe, excel in checks:
            e = rel_err(pipe, excel)
            flag = ""
            if math.isfinite(e):
                n_cmp += 1
                if e <= 0.20:
                    within_20 += 1
                    flag = "OK"
                elif e <= 0.50:
                    within_50 += 1
                    flag = "DRIFT"
                else:
                    flag = "MISMATCH"
            rows.append(
                {
                    "nutrient": nid,
                    "param": name,
                    "pipeline": pipe,
                    "excel": excel,
                    "rel_err": "" if not math.isfinite(e) else round(e, 4),
                    "flag": flag,
                }
            )
            if flag in ("MISMATCH", "DRIFT"):
                print(f"{nid:<22} {name:<10} {pipe:10.4g} {excel:10.4g} {e:8.2%} {flag}")

    out_csv = Path(__file__).resolve().parent / "calibration_report.csv"
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["nutrient", "param", "pipeline", "excel", "rel_err", "flag"])
        w.writeheader()
        w.writerows(rows)

    print("-" * 72)
    print(f"Nutrients compared: {covered}/{len(NUTRIENT_IDS)}")
    print(f"Parameter pairs compared: {n_cmp}")
    print(f"Within 20% of Excel: {within_20}/{n_cmp} ({100*within_20/max(n_cmp,1):.1f}%)")
    print(f"20–50% drift:        {within_50}/{n_cmp} ({100*within_50/max(n_cmp,1):.1f}%)")
    print(f">50% mismatch:       {n_cmp-within_20-within_50}/{n_cmp}")
    print(f"Wrote {out_csv}")

    print("\nCluster ontology check")
    print(f"  Pipeline clusters ({len(CLUSTERS)}): {', '.join(CLUSTERS)}")
    print(f"  Excel clusters    ({len(EXCEL_CLUSTERS)}): {', '.join(EXCEL_CLUSTERS)}")
    mapped = sum(1 for v in PIPELINE_TO_EXCEL_CLUSTER.values() if v)
    print(f"  Named overlap via alias map: {mapped}/12 pipeline clusters have an Excel counterpart")
    unmapped = [k for k, v in PIPELINE_TO_EXCEL_CLUSTER.items() if v is None]
    if unmapped:
        print(f"  Unmapped pipeline clusters: {unmapped}")

    # Calibration is expected to FAIL the tight match gate; report is the deliverable.
    # Exit 0 so the report can be inspected; print a clear verdict.
    match_rate = within_20 / max(n_cmp, 1)
    if match_rate >= 0.8:
        print("\nVERDICT: parameter registry largely aligned with Excel.")
        return 0
    print("\nVERDICT: equations are implemented, but default parameters are NOT")
    print("         calibrated to HealthAI_v31.xlsx (category rules vs 80-row registry).")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_report())

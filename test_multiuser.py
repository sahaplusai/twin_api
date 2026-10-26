"""
Multi-user API tests. Run from twin_api/: python test_multiuser.py

Copyright (c) 2026 SVT Systems
All rights reserved.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp()) / "twin_test.db"
os.environ["TWIN_DB_PATH"] = str(TMP)
os.environ["TWIN_JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient

import db
from app import app

db.init_db()
client = TestClient(app)


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_register_login_me() -> None:
    r = client.post("/v1/auth/register", json={"email": "a@x.com", "password": "secret123"})
    _ok(r.status_code == 200, r.text)
    token = r.json()["token"]
    r2 = client.post("/v1/auth/login", json={"email": "a@x.com", "password": "secret123"})
    _ok(r2.status_code == 200, r2.text)
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    _ok(me.status_code == 200 and me.json()["email"] == "a@x.com", me.text)


def test_duplicate_email() -> None:
    client.post("/v1/auth/register", json={"email": "dup@x.com", "password": "secret123"})
    r = client.post("/v1/auth/register", json={"email": "dup@x.com", "password": "secret123"})
    _ok(r.status_code == 409, r.text)


def test_unauthorized() -> None:
    r = client.get("/v1/latest")
    _ok(r.status_code == 401, r.text)


def test_two_users_isolated_and_persist() -> None:
    t1 = client.post("/v1/auth/register", json={"email": "u1@x.com", "password": "secret123"}).json()["token"]
    t2 = client.post("/v1/auth/register", json={"email": "u2@x.com", "password": "secret123"}).json()["token"]
    h1 = {"Authorization": f"Bearer {t1}"}
    h2 = {"Authorization": f"Bearer {t2}"}
    client.post("/v1/meals", headers=h1, json={"time_minutes": 480, "doses": {"protein_g": 30, "iron_mg": 8}})
    client.post("/v1/meals", headers=h2, json={"time_minutes": 480, "doses": {"sugar_added_g": 60}})
    s1 = client.post(
        "/v1/simulate",
        headers=h1,
        json={"duration_after_last_meal_min": 120, "timestep_min": 20, "include_timeseries": False},
    )
    _ok(s1.status_code == 200, s1.text)
    latest = client.get("/v1/latest", headers=h1)
    _ok(latest.status_code == 200, latest.text)
    _ok(latest.json()["has_state"], "u1 state stored")
    latest2 = client.get("/v1/latest", headers=h2)
    _ok(not latest2.json().get("scores"), "u2 has not simulated")
    meals2 = client.get("/v1/meals", headers=h2).json()["meals"]
    _ok(len(meals2) == 1 and "sugar_added_g" in meals2[0]["doses"], "meals isolated")


SUITE = [
    ("AUTH", "register login me", test_register_login_me),
    ("AUTH", "duplicate email", test_duplicate_email),
    ("AUTH", "missing token", test_unauthorized),
    ("ISO", "two users + persist", test_two_users_isolated_and_persist),
]


def run_all() -> int:
    passed = failed = 0
    print(f"{'ID':<8} {'TEST':<28} RESULT")
    print("-" * 50)
    for eq, name, fn in SUITE:
        try:
            fn()
            print(f"{eq:<8} {name:<28} PASS")
            passed += 1
        except Exception as exc:
            print(f"{eq:<8} {name:<28} FAIL  {exc}")
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_all())

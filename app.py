"""
HealthPlus AI v31 — FastAPI bridge for Layers A–D.

Run:
    uvicorn app:app --app-dir /home/medmodel/twin_api --host 0.0.0.0 --port 8080

Copyright (c) 2026 SVT Systems
All rights reserved.    
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, HTTPException
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import auth
import db
from pipeline import CLUSTERS, NUTRIENT_IDS, run_pipeline


def _model_dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def _model_schema(cls):
    if hasattr(cls, "model_json_schema"):
        return cls.model_json_schema()
    return cls.schema()


app = FastAPI(
    root_path="/healthai/api",
    title="HealthPlus AI v31 Twin API",
    description="Multi-user A–D twin for mobile clients. Anonymous /simulate remains for tools.",
    version="1.1.0",
    docs_url=None,
    redoc_url=None,
)


@app.get("/docs", include_in_schema=False)
def custom_swagger() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="HealthPlus AI v31 Twin API",
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css",
    )


@app.get("/redoc", include_in_schema=False)
def custom_redoc() -> HTMLResponse:
    # Pin a release. FastAPI default redoc@next is often served as text/plain on jsDelivr.
    return get_redoc_html(
        openapi_url="/openapi.json",
        title="HealthPlus AI v31 Twin API",
        redoc_js_url="https://unpkg.com/redoc@2.1.3/bundles/redoc.standalone.js",
    )


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


class MealIn(BaseModel):
    time_minutes: int = Field(..., ge=0, le=10080, description="Minutes since midnight (or sim t0)")
    doses: Dict[str, float] = Field(default_factory=dict, description="Nutrient id → dose")


class SimulateRequest(BaseModel):
    meals: List[MealIn]
    duration_after_last_meal_min: int = Field(480, ge=30, le=2880)
    timestep_min: int = Field(10, ge=1, le=60)
    mvpa_min: float = Field(60.0, ge=0, le=400)
    sedentary_min: float = Field(480.0, ge=0, le=1440)
    sleep_start_min: float = Field(1380, ge=0, le=1440)
    sleep_end_min: float = Field(420, ge=0, le=1440)
    f_cook: float = Field(1.0, ge=0.5, le=1.5)
    labs: Dict[str, float] = Field(default_factory=dict, description="Unused by LayerABCD stack")
    include_timeseries: bool = True
    timeseries_stride: int = Field(1, ge=1, le=60)
    bari_type: Optional[str] = Field(None, description="None, SG, or RYGB")
    glp1_active: bool = False
    selected_nutrients: Optional[List[str]] = None
    initial_C1: Union[Dict[str, float], float] = 0.0
    initial_C2: Union[Dict[str, float], float] = 0.0
    initial_Z: Optional[Dict[str, float]] = None
    enable_a7: bool = False


class SimulateResponse(BaseModel):
    meta: Dict[str, Any]
    layer_a: Dict[str, Any]
    layer_b: Dict[str, Any]
    layer_c: Dict[str, Any]
    layer_d: Dict[str, Any]
    next_state: Dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "healthplus-v31-twin-api",
        "db": db.backend_name(),
    }


@app.get("/schema")
def schema() -> Dict[str, Any]:
    return {
        "nutrient_ids": NUTRIENT_IDS,
        "clusters": CLUSTERS,
        "request": _model_schema(SimulateRequest),
        "outputs": {
            "layer_a": ["t_minutes", "a_i_t"],
            "layer_b": ["C1_t", "C2_t", "clearance", "endpoints"],
            "layer_c": ["Z_k", "Z_k_t"],
            "layer_d": ["D_k", "traffic_lights", "trends", "CHS", "alerts"],
            "next_state": ["initial_C1", "initial_C2", "initial_Z"],
        },
        "example_request": EXAMPLE_REQUEST,
    }


EXAMPLE_REQUEST = {
    "meals": [
        {
            "time_minutes": 480,
            "doses": {
                "energy_kcal": 650,
                "carb_total_g": 80,
                "protein_g": 25,
                "fat_total_g": 20,
                "fiber_g": 8,
                "sodium_mg": 900,
                "iron_mg": 4,
                "vit_c_mg": 40,
            },
        }
    ],
    "duration_after_last_meal_min": 360,
    "timestep_min": 15,
    "mvpa_min": 45,
    "include_timeseries": True,
    "timeseries_stride": 2,
}


@app.get("/example")
def example() -> Dict[str, Any]:
    return EXAMPLE_REQUEST


class AuthIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)


class ProfileIn(BaseModel):
    bari_type: Optional[str] = None
    glp1_active: bool = False
    mvpa_min: float = 60.0
    sedentary_min: float = 480.0
    sleep_start_min: float = 1380.0
    sleep_end_min: float = 420.0
    f_cook: float = 1.0


class UserSimulateRequest(SimulateRequest):
    meals: List[MealIn] = Field(default_factory=list)
    use_stored_meals: bool = True
    use_stored_state: bool = True
    include_timeseries: bool = False
    persist_state: bool = True


@app.post("/v1/auth/register")
def register(body: AuthIn) -> Dict[str, Any]:
    if "@" not in body.email:
        raise HTTPException(status_code=400, detail="invalid_email")
    if db.get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="email_taken")
    uid = db.create_user(body.email, auth.hash_password(body.password))
    token = auth.issue_token(uid, body.email.strip().lower())
    return {"user_id": uid, "email": body.email.strip().lower(), "token": token}


@app.post("/v1/auth/login")
def login(body: AuthIn) -> Dict[str, Any]:
    row = db.get_user_by_email(body.email)
    if row is None or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="bad_credentials")
    token = auth.issue_token(int(row["id"]), row["email"])
    return {"user_id": int(row["id"]), "email": row["email"], "token": token}


@app.get("/v1/me")
def me(user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    return {"user_id": user["id"], "email": user["email"]}


@app.get("/v1/profile")
def get_profile(user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    return db.get_profile(user["id"])


@app.put("/v1/profile")
def put_profile(body: ProfileIn, user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    return db.set_profile(user["id"], _model_dump(body))


@app.post("/v1/meals")
def post_meal(meal: MealIn, user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    mid = db.add_meal(user["id"], meal.time_minutes, meal.doses)
    return {"id": mid, "time_minutes": meal.time_minutes, "doses": meal.doses}


@app.get("/v1/meals")
def get_meals(user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    return {"meals": db.list_meals(user["id"])}


@app.delete("/v1/meals")
def delete_meals(user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, str]:
    db.clear_meals(user["id"])
    return {"status": "cleared"}


def _merge_sim_payload(user_id: int, req: UserSimulateRequest) -> Dict[str, Any]:
    payload = _model_dump(req)
    meals = [_model_dump(m) for m in req.meals]
    if req.use_stored_meals:
        stored = [{"time_minutes": m["time_minutes"], "doses": m["doses"]} for m in db.list_meals(user_id)]
        meals = stored + meals
    if not meals:
        raise HTTPException(status_code=400, detail="no_meals")
    payload["meals"] = meals
    profile = db.get_profile(user_id)
    if profile:
        payload.setdefault("bari_type", profile.get("bari_type"))
        payload["glp1_active"] = payload.get("glp1_active") or bool(profile.get("glp1_active"))
        if req.mvpa_min == 60.0 and "mvpa_min" in profile:
            payload["mvpa_min"] = profile["mvpa_min"]
        if req.sedentary_min == 480.0 and "sedentary_min" in profile:
            payload["sedentary_min"] = profile["sedentary_min"]
    if req.use_stored_state:
        st = db.get_state(user_id)
        if not req.initial_C1:
            payload["initial_C1"] = st.get("initial_C1") or 0.0
        if not req.initial_C2:
            payload["initial_C2"] = st.get("initial_C2") or 0.0
        if not req.initial_Z:
            payload["initial_Z"] = st.get("initial_Z") or None
    return payload


@app.post("/v1/simulate", response_model=SimulateResponse)
def simulate_user(req: UserSimulateRequest, user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    payload = _merge_sim_payload(user["id"], req)
    try:
        result = run_pipeline(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"pipeline_error: {exc}") from exc
    if req.persist_state:
        last = {
            "CHS": result.get("layer_d", {}).get("outputs", {}).get("CHS"),
            "D_k": result.get("layer_d", {}).get("outputs", {}).get("D_k"),
            "traffic_lights": result.get("layer_d", {}).get("outputs", {}).get("traffic_lights"),
            "alerts": result.get("layer_d", {}).get("outputs", {}).get("alerts"),
            "t_end_min": result.get("meta", {}).get("t_end_min"),
        }
        db.save_state(user["id"], result.get("next_state") or {}, last)
    result.setdefault("meta", {})["user_id"] = user["id"]
    return result


@app.get("/v1/latest")
def latest(user: Dict[str, Any] = Depends(auth.current_user)) -> Dict[str, Any]:
    st = db.get_state(user["id"])
    return {
        "user_id": user["id"],
        "updated_at": st.get("updated_at"),
        "scores": st.get("last_result") or {},
        "has_state": bool(st.get("initial_Z")),
    }


@app.post("/simulate", response_model=SimulateResponse)
def simulate(req: SimulateRequest) -> Dict[str, Any]:
    payload = _model_dump(req)
    payload["meals"] = [_model_dump(m) for m in req.meals]
    try:
        return run_pipeline(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"pipeline_error: {exc}") from exc

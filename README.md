# twin_api

FastAPI bridge for the LayerABCD stack. Multi-user meals and physiological state, plus an anonymous simulate endpoint for tools.

The equations live in `../LayerABCD`. This package authenticates, stores, and calls `original_pipeline.run_original_pipeline`.

## Layout

| File               | Role                                                                  |
| ------------------ | --------------------------------------------------------------------- |
| `app.py`           | Routes. Anonymous `/simulate` and authenticated `/v1/*`.              |
| `auth.py`          | PBKDF2 passwords and HMAC Bearer tokens.                              |
| `db.py`            | SQLite (default) or PostgreSQL. Users, profiles, meals, `next_state`. |
| `pipeline.py`      | Adds `../LayerABCD` to `sys.path` and delegates.                      |
| `requirements.txt` | Pins that run on Python 3.7.                                          |

Guides (in the parent directory):

- `Twin_API_Mobile_User_Guide.docx`
- `Twin_API_Sample_Requests.docx`

## Run

```bash
python -m pip install -r requirements.txt
# set TWIN_JWT_SECRET in any shared environment
uvicorn app:app --host 0.0.0.0 --port 8080
```

`GET /health` returns `{"status":"ok","db":"sqlite"}` (or `"postgresql"`).  
Interactive docs: `/docs` (Swagger, pinned unpkg bundle) and `/redoc`.

## Auth

Register or login once. Store the token. Send it on every `/v1` call.

```http
Authorization: Bearer <token>
```

Do not send `user_id` in later bodies. The token already binds the account.

## Mobile sequence

1. `POST /v1/auth/register` or `POST /v1/auth/login`
2. `PUT /v1/profile` — `bari_type`, `glp1_active`, `mvpa_min`, `sedentary_min`, sleep window, `f_cook`
3. `POST /v1/meals` for each eating event (`time_minutes` since midnight, doses by nutrient id)
4. `POST /v1/simulate` — omit `initial_C1`, `initial_C2`, `initial_Z` on the first call
5. Home screen: `GET /v1/latest` (CHS, lights, \(D_k\), alerts)

Defaults on `/v1/simulate`: use stored meals, use stored state, persist `next_state`, do not return timeseries.

## Endpoints

| Method    | Path                | Auth | Purpose                                       |
| --------- | ------------------- | ---- | --------------------------------------------- |
| GET       | `/health`           | no   | Liveness and which database is open           |
| GET       | `/schema`           | no   | Nutrient ids and cluster names                |
| GET       | `/example`          | no   | Sample simulate body                          |
| POST      | `/simulate`         | no   | Anonymous tool run. Do not use for a patient. |
| POST      | `/v1/auth/register` | no   | Create account and token                      |
| POST      | `/v1/auth/login`    | no   | New token                                     |
| GET       | `/v1/me`            | yes  | Who am I                                      |
| GET / PUT | `/v1/profile`       | yes  | Physiology modifiers                          |
| POST      | `/v1/meals`         | yes  | Append one meal                               |
| GET       | `/v1/meals`         | yes  | List stored meals                             |
| DELETE    | `/v1/meals`         | yes  | Clear this user’s meal log                    |
| POST      | `/v1/simulate`      | yes  | Run A–D; persist `next_state`                 |
| GET       | `/v1/latest`        | yes  | Last scores for the home screen               |

## Database

| Mode             | How                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| SQLite (default) | Leave `TWIN_DB_URL` unset. File is `TWIN_DB_PATH` or `twin.db` next to `db.py`.                   |
| PostgreSQL       | `TWIN_DB_URL=postgresql://user:pass@host:5432/dbname` and `pip install 'psycopg2-binary==2.9.9'`. |

Do not point two processes at one SQLite file. Use Postgres once more than one worker writes meals or state.

## Horizon

`duration_after_last_meal_min` is how far **past the latest meal** the integrator runs, not how long a meal lasts. Typical day: 360–480. Overnight warm-start: 600–720. See Appendix A of the user guide.

## Tests

```bash
python test_multiuser.py
```

Requires the LayerABCD tree as a sibling (`../LayerABCD`) and the packages in `requirements.txt`.

## Environment

| Variable           | Default                  | Meaning                              |
| ------------------ | ------------------------ | ------------------------------------ |
| `TWIN_JWT_SECRET`  | `dev-only-change-me`     | HMAC key. Set this outside a laptop. |
| `TWIN_TOKEN_TTL_S` | 7 days                   | Token lifetime                       |
| `TWIN_DB_PATH`     | `twin.db` beside `db.py` | SQLite file                          |
| `TWIN_DB_URL`      | empty                    | `postgresql://…` selects Postgres    |

## Notes

- First call: omit `initial_C1`, `initial_C2`, `initial_Z`. The server starts at zero and stores `next_state`.
- \(D_k\) at \(Z=0\) is about 14–18, not 0. Compare days or show lights, not a bar from the origin.
- Onboarding beyond the six profile fields is not read by A–D.

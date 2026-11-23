# Tournament Management System — Backend API

Django + Django REST Framework + MySQL API powering the Tournament
Management System (paired with `../tournament-frontend`, a React SPA that
consumes this API through the endpoints listed below).

## Technology stack

| Component    | Version / note |
|--------------|----------------|
| Python       | 3.14 (repo-local venv in `myvenv/`) |
| Django       | 6.1 |
| DRF          | djangorestframework 3.18 |
| MySQL        | `mysqlclient` 2.3, database `tournament_management` |
| CORS         | django-cors-headers 4.9 (localhost/127.0.0.1 ports 5173/5174 only) |

No `requirements.txt` exists in this project — packages were installed
manually into `myvenv`.

## Architecture in one paragraph

Single app `tournaments/`:

- `models.py` — **unmanaged** (`managed = False`) models generated with
  `inspectdb` from the existing MySQL schema. The database is the source
  of truth; Django never creates/migrates these tables. Schema changes
  are SQL-first: write SQL in `sql/`, then update the model to match.
- `authentication.py` — `SessionUserAuthentication`: session-cookie auth
  built on a custom `Users` table (not `django.contrib.auth`), enforcing
  CSRF on unsafe methods.
- `permissions.py` — role constants, legacy role normalization
  (`"Team Manager"` → `team_manager`, unknown roles → fail-closed
  `viewer`), object-level permission helpers.
- `serializers.py` — all validation, incl. password policy wired to
  `AUTH_PASSWORD_VALIDATORS`, duplicate checks, business rules.
- `views.py` — base list/detail API views with `check_write`,
  `save_kwargs` (server-side field overrides) and `after_save` /
  `after_delete` hooks inside a DB transaction; auth endpoints.
- `services.py` — `recompute_standings()`; rebuilds a tournament's
  standings table from recorded results (win 3 / draw 1) after every
  result create/update/delete.
- `tests.py` — 26-case API test suite.

## Setup

1. MySQL must be running with database `tournament_management`
   (XAMPP works: `C:\xampp\mysql\bin\mysqld.exe --defaults-file=...`).
2. Activate the venv: `myvenv\Scripts\activate` (Windows).

### Environment variables (all optional, sensible dev defaults)

| Variable              | Default                     |
|-----------------------|-----------------------------|
| `DJANGO_SECRET_KEY`   | insecure dev fallback       |
| `DJANGO_DEBUG`        | `True`                      |
| `DJANGO_ALLOWED_HOSTS`| `localhost,127.0.0.1`       |
| `DJANGO_DB_NAME`      | `tournament_management`     |
| `DJANGO_DB_USER`      | `root`                      |
| `DJANGO_DB_PASSWORD`  | *(empty)*                   |
| `DJANGO_DB_HOST`      | `127.0.0.1`                 |
| `DJANGO_DB_PORT`      | `3306`                      |

Set `DEBUG=False` publicly without also setting `ALLOWED_HOSTS` and the
app intentionally refuses to serve (fail-safe).

## Running

```bat
myvenv\Scripts\python.exe manage.py runserver
```

Serves on `http://127.0.0.1:8000`. Start MySQL first, then the Django
server, then the frontend dev server (see frontend README).

## Checking / testing

```bat
myvenv\Scripts\python.exe manage.py check
myvenv\Scripts\python.exe manage.py test tournaments
```

The test suite:

- runs against `test_tournament_management` (never the real DB),
- bootstraps the unmanaged app tables itself via the schema editor
  (`setUpModule`), because `managed = False` models ship no migrations,
- truncates between tests, so tests are order-independent,
- covers auth flows, anonymous 401/403 behavior, strict viewer
  read-only enforcement, organizer object-level ownership, team-manager
  scoping, referee assignment scoping, standings recompute/reset,
  duplicate prevention and admin user management.

If a previous run crashed, drop the leftover test DB before rerunning:
`DROP DATABASE IF EXISTS test_tournament_management;`

## API surface

All routes live under `/api/` **with trailing slashes** (`config/urls.py`
maps everything into `tournaments.urls`).

### Auth (session cookie based; CSRF-protected for unsafe methods)

| Method + path            | Purpose |
|--------------------------|---------|
| `GET  /api/auth/csrf/`   | Sets the `csrftoken` cookie (call first) |
| `POST /api/auth/signup/` | Public signup → always `viewer` (throttled 10/min) |
| `POST /api/auth/login/`  | Login; rotates session key (throttled 20/min); 401 on bad credentials |
| `POST /api/auth/logout/` | Flushes the session |
| `GET  /api/auth/me/`     | Current user (401 when anonymous) |

### Data CRUD (list + detail under each resource)

`/api/users/` (admin-only), `/api/tournaments/`, `/api/teams/`,
`/api/players/`, `/api/venues/`, `/api/referees/`, `/api/matches/`,
`/api/results/`, `/api/tournament-teams/`, `/api/match-teams/`,
`/api/match-referees/`, `/api/tournament-standings/`

Status codes: anonymous → **401**; authenticated without permission →
**403**; validation problems → **400** with field errors.

## Authentication model

- Passwords hashed with Django's hasher suite; strength policy enforced
  by `validate_password_strength()` (settings `AUTH_PASSWORD_VALIDATORS`).
- Sessions stored in `django_session`; login rotates the session key
  (session-fixation defense).
- The SPA never sees tokens: it holds only the HttpOnly session cookie
  plus the CSRF cookie, and sends `X-CSRFToken` on writes.

## Six roles and permissions

Backend is the security authority. Role strings are stored lowercase;
legacy spellings are normalized and unknown roles fail closed to
`viewer`.

| Role            | Write permissions (everything else is read-only GETs) |
|-----------------|-------------------------------------------------------|
| `admin`         | Everything, incl. user management and role changes    |
| `organizer`     | Tournaments **they organize** (assigned server-side); supports CRUD on related tournament data |
| `referee`       | Results **only for matches they are assigned to** (create/edit); no result delete |
| `team_manager`  | Their **own linked team** (edit) and their team's players (create/edit) |
| `player`        | Nothing — read-only                                   |
| `viewer`        | Nothing — strictly read-only (signup default)         |

Object-level rules live in `views.py::check_write`; hidden UI buttons in
the frontend are convenience only.

## Known conventions / gotchas

- Models are `managed = False`: **never** run `makemigrations` expecting
  app tables; schema changes are SQL-first (see `sql/`).
- Validation runs before some permission checks, so an invalid payload
  from an unauthorized user may return 400 instead of 403 — a valid
  payload always returns the correct 403.
- Referees deleting results is 403 **by design** (matches the frontend,
  which hides the Delete button for referees).
- The DB must exist before almost every management command; `check`
  works without it.

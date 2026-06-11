# Email Scheduler AI — Technical Audit

> **Generated:** 2026-06-08 | **Repository:** `email_scheduler_ai`
> **Commit:** `c9e73fcd6d87143db59356297135404a8123f3f1`
> **Updated:** 2026-06-11 — Added Email Assistant (Send/Reply), Email Intelligence Agent, Analytics Dashboard

---

## 1. Executive Overview

### Problem Solved

Email Scheduler AI automates meeting scheduling and email composition via email. Users send an email requesting a meeting — the system reads it, classifies intent via GPT-4o, creates/updates events on Google Calendar, detects conflicts, proposes alternatives, and sends confirmation or conflict emails back. Users can also request the system to compose new emails or reply to existing email threads. A chat UI provides an interactive alternative to email. Non-calendar business emails are analyzed by the Email Intelligence Agent for categorization, summarization, and information extraction — powering an analytics dashboard. The system eliminates the manual back-and-forth of scheduling meetings and composing emails while providing email intelligence insights.

**Code evidence:**

| File | Lines | Evidence |
|------|-------|----------|
| `app/core/gmail_poller.py` | 59–69 | `poll_gmail()` continuously scans inbox for unread emails |
| `app/agents/email_agent.py` | 73–121 | `process_email()` uses GPT-4o to classify intent |
| `app/agents/calendar_agent.py` | 68–120 | `process_schedule()` creates Google Calendar events |
| `app/agents/notification_agent.py` | 309–369 | `send_notification()` replies with confirmation/conflict emails |
| `app/agents/chat_agent.py` | 151–200 | `chat()` provides interactive scheduling and email composition via LLM |
| `app/agents/email_intelligence_agent.py` | ~225 | `process_email()` classifies non-calendar emails, generates summaries, extracts structured data |

### Target Users

| User Type | How They Interact | Auth Required |
|-----------|-------------------|---------------|
| Email users | Send email to system Gmail account | None |
| Chat UI users | `GET /ui` → interactive chat | JWT cookie |
| Meeting invitees | Click confirmation/reschedule links in email | Token-based (no login) |

### Primary Workflows

| Workflow | Trigger | Entry Point | Outcome |
|----------|---------|-------------|---------|
| Email → Schedule | Incoming email | `gmail_poller.py:59` | Calendar event created, confirmation sent |
| Email → Reschedule | Incoming email requesting time change | Same | Event updated, reschedule sent |
| Email → Conflict | Incoming email with occupied slot | Same → conflict agent | Alternatives proposed, conflict email sent |
| Email → Spam | Incoming email matching spam patterns | `gmail_poller.py:99` | Ignored, marked read |
| Email → Send | Incoming email requesting email composition | Same | Email composed and sent |
| Email → Reply | Incoming email requesting reply | Same | Reply composed and sent |
| Chat → Schedule | Authenticated user | `api/v1/chat.py:339` | Interactive → calendar event |
| Chat → Send Email | Authenticated user | `api/v1/chat.py:339` | Interactive → email composed and sent |
| Chat → Reply Email | Authenticated user | `api/v1/chat.py:339` | Interactive → reply composed and sent |
| Confirmation Link | Invitee clicks link | `api/v1/chat.py:458-639` | Pending invite resolved |
| Webhook → Schedule | Gmail push notification | `api/v1/webhook.py:9` | Pipeline run immediately |
| Email → Intelligence | Non-calendar email (intent="other") | `orchestrator.py` → `email_intelligence_agent.py` | Email categorized, summarized, stored in SQLite |
| Dashboard Analytics | Authenticated user | `api/v1/chat.py:dashboard_email_stats` | Email category statistics for dashboard |

---

## 2. Architecture Overview

See `docs/architecture_overview.md` for Mermaid diagrams and data flow walkthroughs.

### Component Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL SERVICES                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────┐               │
│  │  OpenAI  │  │ Google Gmail │  │ Google Calendar│               │
│  │  GPT-4o  │  │   API v1     │  │    API v3      │               │
│  └────┬─────┘  └──────┬───────┘  └───────┬────────┘               │
└───────┼───────────────┼──────────────────┼─────────────────────────┘
        │               │                  │
        │  ┌────────────┼──────────────────┼──────────────────┐
        │  │            │   FastAPI Server │                  │
        │  │            │   (app/main.py)  │                  │
        │  │            └──────┬───────────┘                  │
        │  │                   │                              │
        │  │    ┌──────────────┼──────────────┐               │
        │  │    │              │              │               │
        │  │  ┌─▼──────┐  ┌───▼────┐  ┌──────▼──────┐       │
        │  │  │ Auth   │  │ Chat   │  │   Webhook   │       │
        │  │  │ API v1 │  │ API v1 │  │   API v1    │       │
        │  │  └──┬─────┘  └───┬────┘  └──────┬──────┘       │
        │  │     │            │              │               │
        │  │  ┌──▼─────┐  ┌───▼────┐  ┌──────▼──────┐       │
        │  │  │ JWT    │  │ Chat   │  │  Gmail      │       │
        │  │  │ Auth   │  │ Agent  │  │  Webhook    │       │
        │  │  └────────┘  └───┬────┘  └──────┬──────┘       │
        │  │                  │              │               │
        │  │                  │    ┌─────────▼──────────┐    │
        │  │                  │    │   Gmail Poller     │    │
        │  │                  │    │ (background task)  │    │
        │  │                  │    └─────────┬──────────┘    │
        │  │                  │              │               │
        │  │               ┌──▼──────────────▼──┐            │
        │  │               │  Spam Filter       │            │
        │  │               └─────────┬──────────┘            │
        │  │                         │                       │
        │  │               ┌─────────▼──────────┐            │
        │  │               │   Email Agent      │            │
        │  │               │   (GPT-4o intent)  │            │
        │  │               └─────────┬──────────┘            │
        │  │                         │                       │
        │  │               ┌─────────▼──────────┐            │
        │  │               │   Orchestrator     │◄───────────┤
        │  │               │   (Router)         │            │
        │  │               └──┬──────┬──────┬───┘            │
        │  │                  │      │      │                │
        │  │         ┌────────▼─┐ ┌──▼──┐ ┌─▼─────────┐     │
        │  │         │ Calendar │ │Conf │ │Evaluation │     │
        │  │         │  Agent   │ │lict │ │  Agent    │     │
        │  │         └────┬─────┘ │Agent│ └─────┬──────┘     │
        │  │              │       └──┬──┘       │            │
        │  │              │          │          │            │
        │  │         ┌────▼──────────▼──────────▼──┐         │
        │  │         │   Notification Agent        │         │
        │  │         │   (Gmail send reply)        │         │
        │  │         └────────────┬────────────────┘         │
        │  │                      │                          │
        │  │         ┌────────────▼────────────────┐         │
        │  │         │   SQLite Database           │         │
        │  │         │   (system_logs, pending_*)  │         │
        │  │         └─────────────────────────────┘         │
        │  │                                                │
        │  │         ┌─────────────────────────────┐        │
        │  │         │   chat_ui.html (Frontend)   │        │
        │  │         │   SPA served at /ui         │        │
        │  │         └─────────────────────────────┘        │
        └──┼────────────────────────────────────────────────┘
```

### Request Lifecycle (Email Path)

```
1. poll_gmail() wakes every GMAIL_POLL_INTERVAL_SECONDS seconds
2. Gmail API: users.messages.list (unread, inbox, last 1d, exclude self)
3. For each message:
   a. _parse_message() → EmailSchema
   b. is_spam(email) → skip if spam
   c. _mark_as_read() immediately
   d. evaluate_and_retry(run_pipeline, email)
      ├─ Attempt 1: run_pipeline(email) → result
      │  └─ evaluate_email(result) → acceptable? return
      ├─ Attempt 2: if !acceptable, wait 2s, retry
      └─ Attempt 3: if still !acceptable, wait 2s, retry
   e. log_event to system_logs
```

### Request Lifecycle (Chat Path)

```
1. User sends POST /chat with {message, history}
2. JWT cookie validated via get_current_user()
3. chat_agent.chat(messages) → GPT-4o
4. Parse <action> tag from reply
5. If action.type == "query_calendar":
   a. Fetch events from Google Calendar
   b. Format + summarize via GPT-4o
6. Return {reply, action} to frontend
7. Frontend displays reply, renders action cards
```

---

## 3. Technology Stack

| Technology | Version | Purpose | Where Used | Key Files |
|-----------|---------|---------|------------|-----------|
| Python | 3.10+ | Runtime | Entire backend | `requirements.txt` |
| FastAPI | unpinned | Web framework | API routing, middleware, startup | `main.py:12-60` |
| Uvicorn | unpinned | ASGI server | App entry point | `main.py:67-68` |
| OpenAI SDK | unpinned | LLM API client | Intent classification, chat, evaluation, email composition | `email_agent.py:12`, `chat_agent.py:12` |
| GPT-4o | — | LLM model | Email classification, chat, evaluation, email generation | `email_agent.py:95`, `chat_agent.py:155` |
| google-api-python-client | unpinned | Google service client | Calendar v3, Gmail v1 | `auth.py:62-72` |
| google-auth-oauthlib | unpinned | OAuth flows | System + user authentication | `auth.py:79`, `auth.py:32` |
| PyJWT | unpinned | JWT tokens | User sessions, confirmation tokens | `jwt_auth.py:4` |
| SQLite | stdlib | Database | Logs, pending actions, users | `sqlite.py:7` |
| Pydantic | unpinned | Data validation | Settings, schemas | `config.py:14`, `email.py:6` |
| python-dotenv | unpinned | Environment | `.env` loading | `config.py:2` |
| HTML/CSS/JS | Vanilla | Frontend | Single-page chat UI + dashboard | `chat_ui.html` (1623 lines) |
| Pytest | unpinned | Test framework | All test suites | `pytest.ini`, 178 tests |
| pytest-asyncio | unpinned | Async test support | Async pipeline/API tests | `pytest.ini:1` |
| pytest-cov | unpinned | Coverage | Measurement | `.coveragerc`, `pytest.ini:4` |
| GitHub Actions | — | CI/CD | Test + coverage on push/PR | `.github/workflows/test.yml` |
| Codecov | v4 | Coverage reporting | Upload artifacts | `test.yml:55-58` |

---

## 4. Repository Structure

| Folder / File | Responsibility | Key Contents |
|--------------|----------------|-------------|
| `app/main.py` | Application entry point. Creates FastAPI, wires routers, starts background poller, serves UI + health. | `FastAPI()`, `include_router`, `asyncio.create_task(poll_gmail())`, `@app.get("/ui")`, `@app.get("/health")` |
| `app/core/` | Core infrastructure: config, auth, JWT, logging, polling. | `config.py:Settings`, `auth.py:get_calendar_service/get_gmail_service`, `jwt_auth.py:create_token/decode_token/get_current_user`, `logger.py:log_event`, `gmail_poller.py:poll_gmail` |
| `app/api/v1/` | HTTP API layer by domain. | `auth.py:login/callback/me/logout`, `chat.py:chat/confirm/decline/reschedule/dashboard`, `webhook.py:gmail_webhook` |
| `app/agents/` | AI agent implementations. Stateless, single-responsibility. | 8 agents — see Agent System (Section 9) |
| `app/orchestrator/` | Pipeline orchestration, routing, agent dispatch. | `orchestrator.py:run_pipeline()` |
| `app/schemas/` | Pydantic data models. | `email.py:EmailSchema` |
| `app/db/` | SQLite layer: schema init, CRUD functions. | `sqlite.py:init_db/get_logs/get_stats/get_user_by_email/create_user/insert_pending_*` |
| `app/chat_ui.html` | Frontend SPA. No framework, no build step. | 1623 lines vanilla JS/CSS/HTML |
| `app/tests/` | Test suite: unit (12), integration (5), e2e (1), frontend (1 empty). | `conftest.py` (430 lines shared fixtures) |
| `app/requirements.txt` | Dependency manifest (15 packages, unpinned). | fastapi, uvicorn, openai, google-api-python-client, etc. |
| `pytest.ini` | Test configuration. | `asyncio_mode=auto`, marks, coverage, paths, timeouts |
| `.coveragerc` | Coverage configuration. | source=app, html + term reports |
| `.env.example` | Environment template (17 variables). | OPENAI_API_KEY, GOOGLE_*, JWT_SECRET, etc. |
| `.github/workflows/test.yml` | CI pipeline: test + coverage on push/PR. | Ubuntu, Python 3.11, Codecov |

---

## 5. Backend Architecture

### FastAPI Setup

**File:** `app/main.py` (68 lines)

```python
app = FastAPI(title="Email Scheduler AI")  # Line 12
```

- CORS middleware: all origins, methods, headers (`main.py:20-27`)
- Three routers: `auth_router` (`/auth`), `chat_router` (`/chat`), `webhook_router` (`/webhook`)
- Direct routes: `GET /ui` (FileResponse), `GET /health` (status check)
- Server: `uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)` (`main.py:67-68`)

### Middleware

Only CORS middleware is registered (`main.py:20-27`):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

No rate limiting, no request logging middleware, no compression. Authentication is via FastAPI dependency injection at route level, not middleware.

### Startup Sequence

**File:** `app/main.py:30-46`

`@app.on_event("startup")` executes:

1. **`init_db()`** (`db/sqlite.py:68-118`) — creates 6 tables if they don't exist: `system_logs`, `pending_invites`, `pending_reschedules`, `users`, `email_intelligence`

2. **`asyncio.create_task(poll_gmail())`** — launches background polling loop as long-running coroutine

### Dependency Flow

```
Request → Router → Dependency → Handler

/auth/me     → get_current_user() [jwt_auth.py:57-78] → user dict → response
/chat        → get_current_user() → chat_agent.chat() → {reply, action}
/dashboard/* → get_current_user() → sqlite.get_stats/logs → response
/webhook/*   → (no auth) → run_pipeline() → {...}
/chat/{action}/{token} → (token decode) → SQLite lookup → calendar update
```

### Service Boundaries

Each agent module is independently importable. Cross-agent communication goes through the orchestrator. No shared mutable state.

| Service | Depends On | Exposes |
|---------|-----------|---------|
| `spam_filter` | Nothing | `is_spam(email) → (bool, str)` |
| `email_agent` | `config`, OpenAI SDK | `process_email(email) → dict` |
| `calendar_agent` | `auth`, `config` | `process_schedule/reschedule() → dict` |
| `chat_agent` | `config`, OpenAI SDK, `auth` (query only) | `chat() → {reply, action}`, `evaluate_email() → dict` |
| `conflict_agent` | Google Calendar API (own auth) | `find_alternatives() → dict` |
| `notification_agent` | `auth` (Gmail service) | `send_notification/send_reply() → dict` |
| `evaluation_agent` | `chat_agent.evaluate_email()` | `evaluate_and_retry() → dict` |
| `email_intelligence_agent` | `config`, OpenAI SDK | `process_email(email) → dict` |
| `orchestrator` | All agents | `run_pipeline(email) → dict` |
| `gmail_poller` | `auth`, `spam_filter`, `evaluation_agent`, `orchestrator` | `poll_gmail()` — infinite loop |

---

## 6. API Inventory

| # | Route | Method | Auth Required | Purpose | Source File |
|---|-------|--------|---------------|---------|-------------|
| 1 | `/health` | GET | None | Health check | `main.py:61` |
| 2 | `/ui` | GET | None | Serve chat UI HTML | `main.py:57` |
| 3 | `/auth/login` | GET | None | Google OAuth redirect | `api/v1/auth.py:73` |
| 4 | `/auth/callback` | GET | None (state) | OAuth callback | `api/v1/auth.py:110` |
| 5 | `/auth/me` | GET | JWT cookie | Current user info | `api/v1/auth.py:226` |
| 6 | `/auth/logout` | POST | None | Clear cookie | `api/v1/auth.py:241` |
| 7 | `/chat` | POST | JWT cookie | Interactive chat (schedule, reschedule, send_email, reply_email, query) | `api/v1/chat.py:339` |
| 8 | `/chat/confirm/{token}` | GET | Token | Confirm invite | `api/v1/chat.py:458` |
| 9 | `/chat/decline/{token}` | GET | Token | Decline invite | `api/v1/chat.py:497` |
| 10 | `/chat/reschedule/confirm/{token}` | GET | Token | Confirm reschedule | `api/v1/chat.py:520` |
| 11 | `/chat/reschedule/decline/{token}` | GET | Token | Decline reschedule | `api/v1/chat.py:594` |
| 12 | `/dashboard/stats` | GET | JWT cookie | System statistics | `api/v1/chat.py:693` |
| 13 | `/dashboard/logs` | GET | JWT cookie | Event logs (paginated) | `api/v1/chat.py:715` |
| 14 | `/dashboard/email-stats` | GET | JWT cookie | Email category statistics | `api/v1/chat.py:dashboard_email_stats` |
| 15 | `/dashboard/recent-emails` | GET | JWT cookie | Recent analyzed emails (paginated, sortable) | `api/v1/chat.py:dashboard_recent_emails` |
| 16 | `/webhook/gmail` | POST | None | Gmail push notification | `api/v1/webhook.py:9` |

**Total: 16 endpoints** across 4 route groups.

---

## 7. Authentication & Authorization

### System Authentication (Google OAuth 2.0)

**File:** `app/core/auth.py` (84 lines)

Two service factories:
- `get_gmail_service()` — `auth.py:62-67` — Gmail v1 with `gmail.modify` scope
- `get_calendar_service()` — `auth.py:69-72` — Calendar v3 with `calendar` scope

Both use `_get_credentials()` (`auth.py:27-54`):
- Token caching from `GOOGLE_TOKEN_PATH`
- Auto-refresh on expiry
- Interactive OAuth via `InstalledAppFlow.run_local_server()` if no token exists

Thread-safe via `creds_lock = threading.Lock()` with double-checked locking.

### User Authentication Flow

#### Step 1: Login (`api/v1/auth.py:73-107`)
```
GET /auth/login
  → OAuth2Session with Google client config
  → PKCE: code_verifier (token_urlsafe 64) → SHA256 → code_challenge → base64url
  → Store state + code_verifier in session cookie
  → Redirect to Google consent screen
```

#### Step 2: Callback (`api/v1/auth.py:110-223`)
```
GET /auth/callback?code=...&state=...
  → Validate state (anti-CSRF)
  → Exchange code for tokens via fetch_token()
  → Fetch user info from userinfo endpoint
  → Upsert user in SQLite users table
  → Generate JWT (user_id, email, name, picture)
  → Set as HttpOnly cookie "access_token"
  → Redirect to /ui
```

#### Step 3: Session Validation (`core/jwt_auth.py:57-78`)
```python
def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    payload = decode_token(token)
    user = get_user_by_email(payload["email"])
    if not user:
        raise HTTPException(401)
    return user
```

#### Step 4: Protected Endpoints
Endpoints using `Depends(get_current_user)`:
- `POST /chat` — `chat.py:339`
- `GET /dashboard/stats` — `chat.py:693`
- `GET /dashboard/logs` — `chat.py:715`
- `GET /auth/me` — `auth.py:226`

#### Step 5: Logout (`api/v1/auth.py:241-252`)
```
POST /auth/logout → delete "access_token" cookie → 200 OK
```

### JWT Implementation

**File:** `app/core/jwt_auth.py` (78 lines)

| Function | Purpose | Details |
|----------|---------|---------|
| `create_token(data, expires_delta)` | Sign JWT | HS256, 24h default expiry, `settings.JWT_SECRET` |
| `decode_token(token)` | Validate JWT | Returns payload or raises |
| `get_current_user(request)` | FastAPI dep | Cookie → decode → SQLite lookup → raise 401 |

### Token-Based Confirmation Links (No Login)

Four endpoints use JWT tokens in URLs for email link actions:
- `/chat/confirm/{token}` — `chat.py:458`
- `/chat/decline/{token}` — `chat.py:497`
- `/chat/reschedule/confirm/{token}` — `chat.py:520`
- `/chat/reschedule/decline/{token}` — `chat.py:594`

Tokens are decoded, then the corresponding pending record is looked up in SQLite and acted upon.

---

## 8. Database Design

**Engine:** SQLite via `sqlite3` (stdlib, no ORM)
**File:** `app/db/sqlite.py` (118 lines)
**Location:** `settings.DATABASE_PATH` (configurable via `.env`)

### Table: `system_logs`

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| `agent` | TEXT | NOT NULL | Which component logged |
| `status` | TEXT | NOT NULL | Result status |
| `payload` | TEXT | NOT NULL DEFAULT '{}' | JSON event data |
| `timestamp` | TEXT | NOT NULL DEFAULT (datetime('now')) | ISO 8601 |

**Access:** `log_event()` (INSERT), `get_logs()` (SELECT with pagination), `get_stats()` (COUNT queries)

### Table: `pending_invites`

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| `token` | TEXT | NOT NULL UNIQUE | JWT for confirmation link |
| `email` | TEXT | NOT NULL | Invitee email |
| `event_id` | TEXT | NOT NULL | Calendar event ID |
| `event_data` | TEXT | NOT NULL | JSON event details |
| `timestamp` | TEXT | NOT NULL DEFAULT (datetime('now')) | Creation time |

**Access:** `insert_pending_invite()`, `get_pending_invite()`, `delete_pending_invite()`

### Table: `pending_reschedules`

Identical structure to `pending_invites`. Used for reschedule pending confirmations.

### Table: `users`

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Internal ID |
| `user_id` | TEXT | NOT NULL UNIQUE | Google user ID |
| `email` | TEXT | NOT NULL | Google email |
| `name` | TEXT | NOT NULL | Display name |
| `picture` | TEXT | | Avatar URL |
| `access_token` | TEXT | | OAuth token |
| `refresh_token` | TEXT | | Refresh token |
| `token_expiry` | TEXT | | Token expiration |
| `created_at` | TEXT | NOT NULL DEFAULT (datetime('now')) | Account creation |

**Access:** `get_user_by_email()` (SELECT), `create_user()` (INSERT)

### Table: `email_intelligence`

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| `email_id` | TEXT | NOT NULL | Unique Gmail message ID |
| `sender` | TEXT | NOT NULL | Email sender |
| `subject` | TEXT | NOT NULL | Email subject |
| `category` | TEXT | NOT NULL | Classification (Meeting, Report, Partnership, Support, Announcement, Other) |
| `summary` | TEXT | NOT NULL | Concise Vietnamese bullet summary |
| `extracted_data_json` | TEXT | NOT NULL DEFAULT '{}' | Structured entities (deadlines, owners, projects, etc.) as JSON |
| `importance_score` | INTEGER | NOT NULL DEFAULT 0 | Importance score 0-100 |
| `processed_at` | TEXT | NOT NULL DEFAULT (datetime('now')) | When analysis was performed |

**Access:** `insert_email_analysis()` (INSERT), `get_email_analysis()` (SELECT by email_id), `get_email_statistics()` (COUNT by category), `get_recent_emails()` (SELECT with pagination, sorted by importance)

### Schema Summary

| Table | Records | Purpose | CRUD |
|-------|---------|---------|------|
| `system_logs` | Append-only | Event audit trail | C, R |
| `pending_invites` | Transient | Pending confirmations | C, R, D |
| `pending_reschedules` | Transient | Pending reschedules | C, R, D |
| `users` | Accumulating | User accounts | C, R |
| `email_intelligence` | Append-only | Email analytics | C, R |

---

## 9. Agent System

### Individual Agent Detail

#### 1. Spam Filter Agent
- **File:** `app/agents/spam_filter.py:68-103` — `is_spam()`
- **Approach:** Rule-based keyword matching (no LLM)
- **Keywords:** 13 sender patterns, 24 subject patterns, 7 body patterns
- **Output:** `(is_spam: bool, reason: str)`

#### 2. Email Classification Agent
- **File:** `app/agents/email_agent.py:73-125` — `process_email()`
- **Model:** GPT-4o with `response_format={"type": "json_object"}`, temperature=0
- **Intents:** schedule, reschedule, query, send_email, reply_email, other
- **Output:** `{intent, summary, time, old_time, location, attendees, confidence, raw_time_text}`
- **Failure:** Try/except → `_fallback(reason)` returns intent="other"

#### 3. Calendar Agent
- **File:** `app/agents/calendar_agent.py` (301 lines)
- **Functions:**
  - `process_schedule()` — `68-120`: freebusy check → create event
  - `process_reschedule()` — `197-301`: find old, check new slot → update
- **Helpers:** `_check_conflict()` (`18-28`), `_create_event()` (`31-49`), `_find_events_by_time()` (`52-64`)
- **Time zone:** `Asia/Ho_Chi_Minh`, Calendar ID: `"primary"`

#### 4. Conflict Resolution Agent
- **File:** `app/agents/conflict_agent.py:93-154` — `find_alternatives()`
- **Algorithm:** Generate 30-min candidates, 8:00-18:00, up to 7 days, return up to 3 free slots
- **Note:** Has duplicate auth implementation (`conflict_agent.py:33-46`) vs `app/core/auth.py`

#### 5. Chat Agent
- **File:** `app/agents/chat_agent.py:151-200` — `chat()`
- **Model:** GPT-4o
- **Actions:** `<action>` XML tags with JSON: schedule, reschedule, query_calendar, send_email, reply_email
- **Also:** `evaluate_email()` (`123-148`) — judges pipeline output quality

#### 6. Notification Agent
- **File:** `app/agents/notification_agent.py:309-369` — `send_notification()`
- **Templates (5):** success, conflict, reschedule, reschedule_not_found, error
- **Format:** Plain-text UTF-8, Vietnamese, Unicode box-drawing
- **Send:** Gmail API `users().messages().send()` with base64url encoding

#### 7. Evaluation Agent
- **File:** `app/agents/evaluation_agent.py:32-116` — `evaluate_and_retry()`
- **Retry:** Up to 3 attempts, 2s delay, LLM quality gate via `chat_agent.evaluate_email()`
- **Logging:** Each attempt logged to `system_logs`

#### 8. Email Intelligence Agent
- **File:** `app/agents/email_intelligence_agent.py:57+` — `process_email()`
- **Model:** GPT-4o with `response_format={"type": "json_object"}`, temperature=0
- **Purpose:** Classifies non-calendar business emails, generates summaries, extracts structured data
- **Categories:** Meeting, Report, Partnership, Support, Announcement, Other
- **Output:** `{category, importance_score (0-100), summary, extracted_data}`
- **Fallback:** Try/except → `_fallback(reason)` returns category="Other", importance=0
- **Extracted data:** Deadlines, owners, projects, meeting requests, amounts, URLs

### Agent Comparison

| Agent | LLM | External API | Stateful | Async | Error Recovery |
|-------|-----|-------------|----------|-------|---------------|
| spam_filter | No | No | No | No | Always safe (not-spam fallback) |
| email_agent | GPT-4o | No | No | No | Fallback to intent="other" |
| calendar_agent | No | Calendar v3 | No | No | Error dict |
| conflict_agent | No | Calendar v3 | No | No | Error dict |
| chat_agent | GPT-4o | Calendar v3 (query) | No | No | Friendly error message |
| notification_agent | No | Gmail v1 | No | No | Error dict |
| evaluation_agent | GPT-4o (via chat) | No | No | Yes | Retry loop + fallback |
| email_intelligence_agent | GPT-4o | No | No | No | Fallback to category="Other", importance=0 |

---

## 10. Orchestration Flow

**File:** `app/orchestrator/orchestrator.py`

### `run_pipeline(email: EmailSchema) → dict`

Routing logic:

```
email_agent.process_email(email)
    │
    ├─ intent="schedule" → calendar_agent.process_schedule()
    ├─ intent="reschedule" → calendar_agent.process_reschedule()
    ├─ intent="query" → query calendar events
    ├─ intent="send_email" → handle send email composition
    ├─ intent="reply_email" → handle reply email composition
    └─ intent="other" → skip (no action, or process via email_intelligence_agent)
    │
    ├─ if calendar_result["status"] == "conflict"
    │   → conflict_agent.find_alternatives()
    │
    └─ notification_agent.send_notification(email, email_result, calendar_result, conflict_result)
```

### Retry Flow

**File:** `app/agents/evaluation_agent.py:32-116`

The Gmail poller wraps pipeline execution:

```python
final_result = await evaluate_and_retry(
    pipeline_fn=run_pipeline,
    email=email_obj,
)
```

Each attempt:
1. Run pipeline → result
2. `chat_agent.evaluate_email(result)` → GPT-4o → `{acceptable: true/false}`
3. If acceptable → return
4. If not → wait 2s, retry (max 3 total attempts)
5. Log each attempt to `system_logs`

### Escalation Logic

**Not verified from repository.** No human escalation, admin intervention, or dead-letter queues exist. The system operates fully autonomously.

---

## 11. Gmail Integration

### Authentication
**File:** `app/core/auth.py:62-67` — `get_gmail_service()`

Scope: `https://www.googleapis.com/auth/gmail.modify`. Thread-safe lazy init with token caching.

### Polling
**File:** `app/core/gmail_poller.py:59-143` — `poll_gmail()`

- Async infinite loop started at startup via `asyncio.create_task()`
- Query: `newer_than:1d is:unread in:inbox -from:{ORGANIZER_EMAIL}`, max 10 results
- Per-message: parse → EmailSchema → spam check → mark read → pipeline → log
- Error resilience: per-message try/except, outer try/except continues loop
- Interval: `GMAIL_POLL_INTERVAL_SECONDS` (env-configurable)

### Webhooks
**File:** `app/api/v1/webhook.py:9-23` — `POST /webhook/gmail`

Push-based trigger bypassing poll interval. Processes newest unread emails immediately.

### Email Parsing
**File:** `app/core/gmail_poller.py:18-44` — `_parse_message()`

- Fetches `raw` format message via Gmail API
- Base64 decode → `email.message_from_bytes()` (stdlib)
- Extracts: `text/plain` part (falls back to payload decode), `From`, `Subject`, `Date`
- HTML-only emails: body will be empty

### Sender Exclusion
Query excludes `settings.ORGANIZER_EMAIL` to prevent self-processing loops.

### Notification Sending
**File:** `app/agents/notification_agent.py:265-272` — `_send()`

`users().messages().send()` with base64url encoding. Sent from the system account.

---

## 12. Google Calendar Integration

### Event Creation
**File:** `app/agents/calendar_agent.py:31-49` — `_create_event()`

- Fields: summary, start/end (ISO 8601, `Asia/Ho_Chi_Minh`), location, attendees (filtered for "@"), description
- Reminders: email 24h before, popup 30m before
- `sendUpdates="all"`, Calendar ID: `"primary"`

### Event Updates
**File:** `app/agents/calendar_agent.py:266-271` — in `process_reschedule()`

`service.events().update()` — modifies start/end fields.

### Event Discovery
**File:** `app/agents/calendar_agent.py:52-64` — `_find_events_by_time()`

Searches ±1h window around target time. Returns list.

### Conflict Checking
**File:** `app/agents/calendar_agent.py:18-28` — `_check_conflict()`

`service.freebusy().query()` — returns busy slots in time range.

### Alternative Slot Finding
**File:** `app/agents/conflict_agent.py:93-154` — `find_alternatives()`

30-min increments, 8:00-18:00, max 7 days, returns up to 3 free slots.

### Calendar Query for Chat
**File:** `app/agents/chat_agent.py:68-99` — `_fetch_upcoming_events()`

Fetches upcoming events (default 7 days, max 20) for display in chat UI.

---

## 13. Frontend Architecture

**File:** `app/chat_ui.html` — 1623 lines, single-file SPA

### Technology
Vanilla HTML, CSS, JavaScript. No framework, no bundler, no TypeScript. Google Identity Services for OAuth only.

### UI Structure

| Section | Visible When | Components |
|---------|-------------|------------|
| Login screen | No `access_token` cookie | Google login button |
| Chat interface | Has `access_token` cookie | Message history, text input, send button, action cards |
| Dashboard | Has `access_token` cookie | Stats cards, log table (paginated) |

### Authentication State
JS checks for `access_token` cookie on load. `credentials: "include"` on all `fetch()` calls.

### API Communication
- `POST /chat` — send message, receive reply + action
- `GET /chat/{confirm,decline,reschedule}/{token}` — action links
- `GET /dashboard/stats`, `GET /dashboard/logs` — dashboard data
- `GET /auth/me` — verify login state

---

## 14. Feature Inventory

See `docs/feature_inventory.md` for the complete listing.

### Implemented (17 features)

| Feature | Entry Point | Key Files |
|---------|------------|-----------|
| Google OAuth Login | `GET /auth/login` | `api/v1/auth.py`, `core/jwt_auth.py` |
| JWT Session | `GET /auth/me` | `core/jwt_auth.py` |
| Interactive Chat Scheduling | `POST /chat` | `agents/chat_agent.py:chat()` |
| Email Intent Classification | Pipeline entry | `agents/email_agent.py:process_email()` |
| Calendar Event Create | Pipeline schedule | `agents/calendar_agent.py:process_schedule()` |
| Calendar Event Reschedule | Pipeline reschedule | `agents/calendar_agent.py:process_reschedule()` |
| Email Composition (Send) | Pipeline send_email | `agents/notification_agent.py:send_notification()` |
| Email Reply Assistant | Pipeline reply_email | `agents/notification_agent.py:send_notification()` |
| Conflict Detection | During create/reschedule | `calendar_agent.py:_check_conflict()` |
| Alternative Slot Finder | After conflict | `agents/conflict_agent.py:find_alternatives()` |
| Notification Emails | After every pipeline | `agents/notification_agent.py:send_notification()` |
| Spam Filtering | Before pipeline | `agents/spam_filter.py:is_spam()` |
| Gmail Polling | Startup | `core/gmail_poller.py:poll_gmail()` |
| Webhook Ingestion | `POST /webhook/gmail` | `api/v1/webhook.py` |
| Confirmation Links | `GET /chat/{action}/{token}` | `api/v1/chat.py` (4 endpoints) |
| Pipeline Evaluation + Retry | Wraps pipeline | `agents/evaluation_agent.py:evaluate_and_retry()` |
| System Dashboard | `GET /dashboard/*` | `api/v1/chat.py` + `db/sqlite.py` |
| Audit Logging | Throughout pipeline | `core/logger.py:log_event()` |

### Not Implemented

| Feature | Evidence |
|---------|----------|
| Recurring meetings | No `recurrence` field in event creation |
| Multi-calendar | Only `"primary"` calendar |
| Rate limiting | No middleware or decorator |
| User roles / admin | All users have equal access |
| Email threading | No `In-Reply-To` headers |
| Real-time notifications (WS/SSE) | No WebSocket endpoints |

---

## 15. Data Flow Walkthroughs

See `docs/architecture_overview.md` for detailed step-by-step walkthroughs of:
1. Login Flow
2. Schedule Meeting Flow (Email)
3. Send Email Flow
4. Reply Email Flow
5. Reschedule Flow
6. Incoming Email Flow (Webhook)
7. Dashboard Statistics Flow

---

## 16. Testing Strategy

### Overview
- **Config:** `pytest.ini` — asyncio auto mode, strict markers, coverage
- **Shared fixtures:** `tests/conftest.py` — 430 lines
- **Total tests:** 178 (enumerated in `all_test_cases.txt`)

### Unit Tests (12 files)

| Test File | Target | Key Focus |
|-----------|--------|-----------|
| `test_calendar_agent.py` | `calendar_agent.py` | Schedule, reschedule, conflicts |
| `test_chat_agent.py` | `chat_agent.py` | Chat responses, action extraction |
| `test_conflict_agent.py` | `conflict_agent.py` | Alternative slots, working hours |
| `test_email_agent.py` | `email_agent.py` | Intent classification, time parsing |
| `test_evaluation_agent.py` | `evaluation_agent.py` | Retry logic, acceptability |
| `test_inquiry_handler.py` | Orchestrator | Calendar inquiry flow |
| `test_jwt_auth.py` | `jwt_auth.py` | Token create, decode, expiry |
| `test_logger.py` | `logger.py` | Event logging |
| `test_notification_agent.py` | `notification_agent.py` | All email templates |
| `test_orchestrator.py` | `orchestrator.py` | Pipeline routing |
| `test_spam_filter.py` | `spam_filter.py` | Keyword matching |
| `test_sqlite.py` | `sqlite.py` | Table CRUD for all 5 tables |

### Integration Tests (5 files)

| Test File | Target |
|-----------|--------|
| `test_api_chat.py` | `POST /chat` endpoint |
| `test_api_dashboard.py` | `GET /dashboard/*` endpoints |
| `test_api_webhook.py` | `POST /webhook/gmail` endpoint |
| `test_auth_flow.py` | Full OAuth login flow |
| `test_gmail_poller.py` | Polling cycle |

### E2E Tests (1 file)

| Test File | Key Scenarios |
|-----------|--------------|
| `test_full_pipeline.py` | Schedule, reschedule, send_email, reply_email, spam, inquiry, retry on failure |

### Frontend Tests
`test_chat_ui.py` — **empty file (0 bytes)** — no frontend tests implemented.

### Coverage
`pytest --cov=app --cov-report=xml --cov-report=html` in CI. Uploaded to Codecov.

---

## 17. CI/CD Pipeline

**File:** `.github/workflows/test.yml`

```yaml
name: Test
on: [push, pull_request]
```

| Attribute | Value |
|-----------|-------|
| Runner | `ubuntu-latest` |
| Python | 3.11 |
| Steps | checkout → setup-python → pip install → pytest --cov → codecov upload |
| Codecov | `codecov-action@v4` with `${{ secrets.CODECOV_TOKEN }}` |

No deployment step. No branch filtering. CI runs on every push and PR.

---

## 18. Security Review

### OAuth Protections

| Aspect | Implementation | File |
|--------|---------------|------|
| Token storage | Local JSON file (`GOOGLE_TOKEN_PATH`) | `auth.py:41` |
| Token refresh | `creds.refresh(Request())` on expiry | `auth.py:43-45` |
| Thread safety | `threading.Lock` double-checked locking | `auth.py:39,47-53` |
| User PKCE | `code_verifier` → SHA256 → `code_challenge` | `auth.py:74-81` |
| Anti-CSRF | State cookie validated on callback | `auth.py:112-115` |

### JWT Protections

| Aspect | Implementation | File |
|--------|---------------|------|
| Algorithm | HS256 (HMAC-SHA256) | `jwt_auth.py:22` |
| Expiry | 24 hours default | `jwt_auth.py:16` |
| Cookie | HttpOnly `access_token` | `auth.py:214` |
| Secret | `JWT_SECRET` from `.env` | `config.py` |

### Access Controls

| Protection | Active On |
|-----------|-----------|
| JWT required | `POST /chat`, `GET /dashboard/*`, `GET /auth/me` |
| Token validation | `/chat/{action}/{token}` endpoints (no login required) |
| Public access | `/health`, `/ui`, `/auth/*`, `/webhook/gmail` |

### Limitations

- No token revocation database
- No rate limiting
- All users see all dashboard data (no data isolation)
- `secure=True` flag on cookies not verified from repository

---

## 19. Known Limitations

| # | Limitation | Evidence | Severity |
|---|-----------|----------|----------|
| 1 | Single calendar only | `CALENDAR_ID = "primary"` hardcoded | Medium |
| 2 | Duplicate auth in conflict_agent | Own `_get_service()` vs `app/core/auth.py` | Low |
| 3 | No rate limiting | No rate limit middleware or decorators | High |
| 4 | No token revocation | Logout only clears cookie, JWT still valid | Medium |
| 5 | No email threading | No `In-Reply-To` or `References` headers | Low |
| 6 | Reschedule uses first match only | `events[0]` used, no disambiguation | Medium |
| 7 | No recurring meeting support | No `recurrence` field in event creation | Medium |
| 8 | Fixed 60-minute duration | `DEFAULT_DURATION = 60`, not configurable | Low |
| 9 | Keyword-only spam filter | No ML, no sender reputation | Low |
| 10 | No database migrations | Only `CREATE TABLE IF NOT EXISTS` | Low |
| 11 | Max 10 results per poll | `maxResults=10`, `newer_than:1d` | Low |
| 12 | Working hours hardcoded | 8:00-18:00, not configurable | Low |
| 13 | All users see all data | Dashboard not filtered by user | Medium |
| 14 | No input validation on chat | No length limits or content filtering found | Low |
| 15 | Only text/plain email body | HTML-only emails get empty body | Medium |
| 16 | No graceful shutdown | No `@app.on_event("shutdown")` | Low |
| 17 | Shallow health check | Static `{"status": "ok"}` only | Low |

---

## 20. Executive Summary

### Maturity Level
**Prototype / Alpha.** Functional proof-of-concept for AI-driven email scheduling and email composition assistance. All core workflows implemented and testable. Architecture is clean and modular. Not production-ready for multi-user deployment.

### Implemented Capabilities (18 features)
Dual-input scheduling (email + chat), full Calendar CRUD (schedule + reschedule), email composition (send + reply), conflict resolution, automated emails (5 templates), LLM evaluation with retry, Google OAuth + JWT, audit logging, dashboard, spam filtering, webhook ingestion.

### Architectural Strengths
1. Clean agent separation with single responsibilities
2. Dual input channels (polling + webhook + chat)
3. LLM-based evaluation with retry
4. Standard FastAPI patterns
5. Comprehensive test suite (178 tests)
6. CI pipeline with coverage reporting

### Readiness Assessment

| Dimension | Rating | Notes |
|-----------|--------|-------|
| Core functionality | ✅ Functional | All flows work end-to-end |
| Error handling | ⚠️ Basic | Try/except wrapping only |
| Security | ⚠️ Needs review | No rate limiting, no token revocation |
| Scalability | ⚠️ Limited | Single SQLite, single calendar |
| Observability | ⚠️ Basic | SQLite logs + dashboard only |
| Multi-tenancy | ❌ Not implemented | All users share all data |
| Production readiness | ❌ Not ready | Significant hardening required |

---

*End of Technical Audit — Email Scheduler AI*
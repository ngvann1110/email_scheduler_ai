# SOFTWARE ARCHITECTURE REPORT

## Email Scheduler AI — Hệ thống Trợ lý Quản lý Lịch & Email Thông minh

---

## 1. SYSTEM OVERVIEW

### Project Purpose

Email Scheduler AI là một hệ thống trợ lý thông minh (AI-powered) tự động hóa việc quản lý lịch họp và email. Hệ thống nhận email đến (qua Gmail webhook), sử dụng AI (GPT-4o / GPT-4o-mini) để phân tích nội dung, trích xuất ý định (đặt lịch, dời lịch, hủy lịch, soạn email, trả lời email...), kiểm tra khả dụng trên Google Calendar, tự động tạo/sửa/hủy sự kiện, và gửi email phản hồi tự động cho người gửi. Hệ thống cũng cung cấp một giao diện Chat UI và Dashboard để người dùng tương tác, xác nhận, và giám sát toàn bộ quy trình.

### Main Business Functions

| Chức năng | Mô tả |
| --- | --- |
| **Phân tích email (Email Intelligence)** | Nhận email thô, dùng GPT-4o phân loại ý định, danh mục, độ ưu tiên, trích xuất thời gian, địa điểm, người tham dự, sentiment |
| **Quản lý lịch họp (Calendar Management)** | Tạo lịch mới, dời lịch, kiểm tra xung đột lịch, tìm khung giờ trống trên Google Calendar |
| **Soạn & Gửi email (Email Composition)** | Sử dụng GPT-4o-mini soạn email trả lời chuyên nghiệp, hỗ trợ nhận dạng ngôn ngữ (vi/en/ja/ko) |
| **HITL Pipeline (Human-in-the-Loop)** | Khi AI không chắc chắn hoặc cần xác nhận, lưu vào pending_actions để người dùng duyệt qua Dashboard |
| **Dashboard & Analytics** | Hiển thị pending actions, email statistics, executive briefing, risk detection, priority recommendation, productivity insights |
| **Chat UI (Conversational)** | Người dùng có thể chat trực tiếp bằng tiếng Việt để đặt lịch, xem lịch, dời lịch, soạn email |

### Main User Roles

| Role | Description |
| --- | --- |
| **Người dùng cuối (End User)** | Đăng nhập qua Google OAuth, sử dụng Chat UI và Dashboard để quản lý email và lịch |
| **Người gửi email (External Sender)** | Gửi email đến địa chỉ được cấu hình, hệ thống tự động xử lý và phản hồi |

### Major Workflows

1. **Webhook Pipeline:** Email → Webhook → EmailAgent (phân tích GPT-4o) → Orchestrator (routing) → CalendarAgent (check/create) → NotificationAgent (gửi phản hồi)
2. **HITL Flow:** Email không chắc chắn → Lưu pending_actions → Người dùng Dashboard → Xác nhận (Accept/Reject/Suggest) → CalendarAgent → NotificationAgent
3. **Chat Flow:** User message → ChatAgent (GPT-4o) → Extract action JSON → Execute (Schedule/Reschedule/Query/Send Email)
4. **Executive Intelligence:** ChiefOfStaffAgent → gather_context (DB + Calendar) → Skills (Briefing/Risk/Priority/Waiting/Deadline/Productivity)

### Technologies Used

| Category | Technology |
| --- | --- |
| **Backend Framework** | FastAPI (Python 3.10+) |
| **AI/LLM** | OpenAI GPT-4o, GPT-4o-mini |
| **Database** | SQLite (app/db/sqlite.py — file-based) |
| **External APIs** | Google Gmail API, Google Calendar API, Google OAuth 2.0 |
| **Authentication** | Google OAuth + JWT (python-jose, PyJWT) |
| **Validation** | Pydantic v2 |
| **HTTP Client** | httpx |
| **Server** | Uvicorn |
| **Frontend** | Single HTML file (chat_ui.html) — Vanilla HTML/CSS/JS |
| **CI/CD** | GitHub Actions (.github/workflows/test.yml) |
| **Testing** | Pytest with pytest-asyncio |

---

## 2. SOFTWARE ARCHITECTURE

### Architecture Style: Layered Architecture + Agent-based AI Pipeline

The system follows a **Layered Architecture** with a distinct **Agent-based pipeline** pattern for email processing. It is **not** a strict Clean Architecture or MVC, but rather a pragmatic layered design with:

- **Presentation Layer:** Single-page HTML app (chat_ui.html) serving as both Chat UI and Dashboard
- **API Layer:** FastAPI routers (auth, chat, dashboard, webhook)
- **Application/Orchestration Layer:** Orchestrator (run_pipeline) + Agents (email, calendar, chat, notification, chief_of_staff, evaluation)
- **Domain/Schema Layer:** Pydantic models (schemas/email.py, schemas/chat.py)
- **Infrastructure Layer:** Database access (db/sqlite.py), External API wrappers (core/auth.py, Google APIs), Configuration (core/config.py)

### Architecture Diagram (Textual)

```
┌─────────────────────────────────────────────────┐
│              PRESENTATION LAYER                  │
│    chat_ui.html (Single-Page App)                │
│    - Chat UI (chat_tab)                          │
│    - Dashboard (dashboard_tab)                   │
└──────────────────────┬──────────────────────────┘
                       │ HTTP / JWT Cookie
┌──────────────────────▼──────────────────────────┐
│                 API LAYER (FastAPI)              │
│  /api/v1/auth/*    (Google OAuth + JWT)          │
│  /api/v1/chat      (Chat endpoint)               │
│  /api/v1/dashboard/* (Dashboard endpoints)        │
│  /webhook/gmail    (Gmail webhook)                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│            APPLICATION / AGENT LAYER             │
│  agents/email_agent.py     ← GPT-4o classify     │
│  agents/chat_agent.py      ← GPT-4o chat + action│
│  agents/calendar_agent.py  ← Google Calendar API │
│  agents/notification_agent.py ← Gmail API send   │
│  agents/chief_of_staff_agent.py ← GPT-4o-mini    │
│  agents/evaluation_agent.py ← evaluate results   │
│  orchestrator/orchestrator.py ← Pipeline router   │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              DOMAIN / SCHEMA LAYER               │
│  schemas/email.py    (EmailSchema Pydantic)       │
│  schemas/chat.py     (Chat Pydantic)              │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│            INFRASTRUCTURE LAYER                   │
│  db/sqlite.py        (SQLite CRUD operations)     │
│  core/config.py      (Settings from .env)          │
│  core/auth.py        (Google API service builders) │
│  core/jwt_auth.py    (JWT creation/verification)   │
│  core/logger.py      (Event logging)               │
└─────────────────────────────────────────────────┘
```

### Why This Architecture

- **FastAPI** was chosen for async support, auto-documentation (OpenAPI), and Pydantic integration
- **Agent-based pipeline** separates concerns: each agent handles one external service or AI task
- **Orchestrator** centralizes routing logic, making the pipeline easy to modify
- **Single-file HTML frontend** keeps deployment simple (no separate Node.js build)
- **SQLite** avoids external database dependencies for a graduation thesis project
- **Google OAuth + JWT** provides secure authentication with Calendar/Gmail scope access

### Dependencies Between Layers

- API Layer → Application Layer (imports agents, orchestrator)
- API Layer → Infrastructure Layer (imports jwt_auth, logger)
- Application Layer → Infrastructure Layer (imports config, auth, db)
- Application Layer → Domain Layer (imports schemas)
- Orchestrator → All Agents (composes the pipeline)

---

## 3. PACKAGE ANALYSIS

### Package List

| Package | Path | Purpose | Depends On |
| --- | --- | --- | --- |
| **main** | `app/main.py` | FastAPI application entry point, CORS, router mounting | api, core |
| **agents** | `app/agents/` | AI-powered agents for email/calendar/chat/notification/chief_of_staff/evaluation | core, schemas, openai, google-api |
| **api** | `app/api/v1/` | REST API routers (auth, chat, dashboard, webhook) | core, agents, orchestrator, schemas, db |
| **core** | `app/core/` | Configuration, auth helpers, JWT, logging | pydantic-settings, google-auth, python-jose |
| **db** | `app/db/` | SQLite database access layer (CRUD operations) | sqlite3 |
| **orchestrator** | `app/orchestrator/` | Central pipeline coordinator routing emails through agents | agents, schemas, db, core |
| **schemas** | `app/schemas/` | Pydantic data models (EmailSchema, ChatRequest, etc.) | pydantic |
| **tests** | `app/tests/` | Unit, integration, E2E test suites | pytest, all packages |

### Dependency Details

```
app/api/         → app/agents/, app/orchestrator/, app/core/, app/db/, app/schemas/
app/agents/      → app/core/, app/db/ (chief_of_staff), openai
app/orchestrator/ → app/agents/, app/core/, app/db/, app/schemas/
app/core/        → (only external libs: google-auth, PyJWT, pydantic-settings)
app/db/          → (only sqlite3 stdlib)
app/schemas/     → (only pydantic)
app/main.py      → app/api/, app/core/
```

### Cross-layer Dependencies

- `app/api/v1/dashboard.py` imports `app/agents/calendar_agent._check_conflict` and `_get_service` — **API crosses directly into Agent layer** (violating strict layering)
- `app/agents/chief_of_staff_agent.py` imports `app/db/sqlite.*` and `app/agents/chat_agent._fetch_upcoming_events` — **Agent crosses into DB and another Agent**
- `app/orchestrator/orchestrator.py` imports from `app/db/sqlite` — **Orchestrator crosses into Infrastructure**

### Circular Dependencies

No circular dependencies detected. All imports form a DAG.

### Architecture Rule Violations

1. **API → Agent internal functions:** `dashboard.py` calls `_check_conflict` and `_get_service` (private functions from `calendar_agent.py`). These should be public.
2. **Agent → Agent dependency:** `chief_of_staff_agent.py` imports `_fetch_upcoming_events` from `chat_agent.py`. This creates coupling between agents.
3. **API contains business logic:** `dashboard.py` has a 1291-line file containing email draft generation (OpenAI calls), free-slot finding, and extensive endpoint logic. Should be split.

---

## 4. UML PACKAGE DIAGRAM DATA

```
Presentation Layer:
  chat_ui.html → api (HTTP/JSON)

API Layer:
  api/auth → core/jwt_auth, core/config, db, schemas
  api/chat → agents/chat_agent, agents/chief_of_staff_agent, core/jwt_auth, db, schemas
  api/dashboard → agents/calendar_agent, core/jwt_auth, db, schemas
  api/webhook → orchestrator, schemas, core/logger

Application/Agent Layer:
  agents/email_agent → core/config, openai
  agents/chat_agent → core/config, core/auth, openai, agents/chief_of_staff_agent
  agents/calendar_agent → core/config, core/auth, googleapiclient
  agents/notification_agent → core/auth, googleapiclient
  agents/chief_of_staff_agent → core/config, db, agents/chat_agent, openai
  agents/evaluation_agent → core/config, openai
  orchestrator → agents/email_agent, agents/calendar_agent, agents/notification_agent, db, schemas

Infrastructure Layer:
  core/config → pydantic_settings
  core/auth → google_auth_oauthlib, googleapiclient
  core/jwt_auth → PyJWT, core/config
  core/logger → (stdlib logging + db)
  db/sqlite → (sqlite3 stdlib)

Domain Layer:
  schemas/email → pydantic
  schemas/chat → pydantic

Tests Layer:
  tests/unit/* → individual agents/core modules
  tests/integration/* → api endpoints
  tests/e2e/* → full pipeline
```

---

## 5. DETAILED PACKAGE DESIGN

### 5.1 Package: `app/agents/`

This is the most important package — contains all AI and external service integration logic.

#### 5.1.1 `app/agents/email_agent.py` — Email Intelligence Agent

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `process_email(email) → dict` | Function (public) | Main entry: phân tích email qua GPT-4o, trả về structured JSON | `openai.OpenAI`, `app.core.config.settings` |
| `_extract_json(raw) → dict` | Function (private) | Trích xuất JSON từ response thô của GPT | regex |
| `_validate_and_normalise(result) → dict` | Function (private) | Validate và chuẩn hóa tất cả các field | Các set VALID_* constants |
| `_fallback(reason) → dict` | Function (private) | Trả về fallback dict khi classification thất bại | — |
| `language_detection_skill(email_result) → dict` | Function (public skill) | Trích xuất ngôn ngữ từ email_result | — |
| `sentiment_analysis_skill(email_result) → dict` | Function (public skill) | Trích xuất sentiment từ email_result | — |
| `priority_scoring_skill(email_result) → int` | Function (public skill) | Tính điểm ưu tiên [0–100] | — |
| `SYSTEM_PROMPT` | Constant (str) | System prompt cho GPT-4o | — |
| `VALID_CATEGORIES`, `VALID_PRIORITIES`, `VALID_INTENTS`, `VALID_LANGUAGES`, `VALID_SENTIMENTS` | Constant sets | Tập giá trị hợp lệ để validation | — |

**Relationships:**
- `process_email` uses `openai.OpenAI` (association)
- `process_email` depends on `app.core.config.settings` (dependency)

#### 5.1.2 `app/agents/calendar_agent.py` — Calendar Management Agent

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `process_schedule(email_result) → dict` | Function (public) | Tạo sự kiện mới trên Google Calendar | `googleapiclient`, `core.auth.get_calendar_service` |
| `process_reschedule(email_result) → dict` | Function (public) | Tìm và dời event cũ sang thời gian mới | `googleapiclient`, `core.auth.get_calendar_service` |
| `check_calendar_availability(email_result) → dict` | Function (public) | Kiểm tra khung giờ có trống không (read-only) | `googleapiclient`, `core.auth.get_calendar_service` |
| `check_reschedule_availability(email_result) → dict` | Function (public) | Kiểm tra việc dời lịch có khả thi không (read-only) | `googleapiclient`, `core.auth.get_calendar_service` |
| `schedule_risk_skill(events) → dict` | Function (public skill) | Phân tích rủi ro lịch trình (back-to-back, ngoài giờ, quá tải) | — |
| `availability_intelligence_skill(days_ahead) → dict` | Function (public skill) | Tính toán free/busy summary cho N ngày tới | `googleapiclient`, `core.auth.get_calendar_service` |
| `_check_conflict(service, start, end) → list` | Function (private) | Gọi Google freebusy API kiểm tra busy slots | `googleapiclient` |
| `_create_event(service, ...) → dict` | Function (private) | Tạo event trên Google Calendar với reminders | `googleapiclient` |
| `_find_events_by_time(service, start, end) → list` | Function (private) | Tìm events trong khoảng thời gian | `googleapiclient` |

**Relationships:**
- All public functions depend on `core.auth.get_calendar_service` (dependency)
- All public functions use `googleapiclient.errors.HttpError` for error handling (association)
- `_create_event`, `_check_conflict`, `_find_events_by_time` are private helpers (composition within module)

#### 5.1.3 `app/agents/chat_agent.py` — Conversational Chat Agent

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `chat(messages) → dict` | Function (public) | Main chat entry: GPT-4o response + action extraction | `openai.OpenAI`, `core.config.settings`, `core.auth.get_calendar_service` |
| `evaluate_email(pipeline_result) → dict` | Function (public) | Đánh giá kết quả pipeline có chấp nhận được không | `openai.OpenAI`, `core.config.settings` |
| `_fetch_upcoming_events(range_days) → list` | Function (module-private) | Lấy danh sách events từ Google Calendar | `googleapiclient`, `core.auth.get_calendar_service` |
| `_format_events(events) → str` | Function (module-private) | Format events thành chuỗi hiển thị | — |
| `_classify_executive_intent(message) → str` | Function (module-private) | Routing executive questions đến ChiefOfStaff | `agents.chief_of_staff_agent` |
| `SYSTEM_PROMPT` | Constant (str) | System prompt cho chat GPT-4o | — |

**Relationships:**
- `chat` depends on `openai.OpenAI` (association)
- `chat` calls `_classify_executive_intent` → delegates to `chief_of_staff_agent.answer_executive_question` (dependency)
- `_fetch_upcoming_events` is imported by `chief_of_staff_agent.gather_context` (cross-agent dependency)

#### 5.1.4 `app/agents/notification_agent.py` — Email Notification Agent

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `send_notification(email_obj, email_result, calendar_result, conflict_result) → dict` | Function (public) | Gửi email thông báo dựa trên kết quả pipeline | `googleapiclient`, `core.auth.get_gmail_service` |
| `send_reply(to_email, subject, body_text) → dict` | Function (public) | Gửi email trả lời đơn giản | `googleapiclient`, `core.auth.get_gmail_service` |
| `_build_success_email(...) → MIMEMultipart` | Function (private) | Build email xác nhận đặt lịch thành công | — |
| `_build_conflict_email(...) → MIMEMultipart` | Function (private) | Build email báo xung đột lịch + gợi ý | — |
| `_build_reschedule_email(...) → MIMEMultipart` | Function (private) | Build email xác nhận dời lịch | — |
| `_build_reschedule_not_found_email(...) → MIMEMultipart` | Function (private) | Build email báo không tìm thấy lịch cũ | — |
| `_build_error_email(...) → MIMEMultipart` | Function (private) | Build email báo lỗi xử lý | — |
| `_send(service, msg) → bool` | Function (private) | Gửi MIME message qua Gmail API | `googleapiclient` |
| `_decode_subject(subject) → str` | Function (private) | Decode subject header (RFC 2047) | — |
| `_format_datetime(iso_str) → str` | Function (private) | Format ISO datetime → tiếng Việt | — |

#### 5.1.5 `app/agents/chief_of_staff_agent.py` — Executive Intelligence Agent

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `answer_executive_question(question, last_view) → dict` | Function (public) | Route executive question đến skill phù hợp | All skills, `gather_context`, `classify_executive_intent` |
| `gather_context(last_view) → dict` | Function (public) | Thu thập toàn bộ context từ DB + Calendar | `app.db.sqlite.*`, `agents.chat_agent._fetch_upcoming_events` |
| `executive_briefing_skill(context) → dict` | Function (public skill) | Tạo executive briefing qua GPT-4o-mini | `openai.OpenAI`, `core.config.settings` |
| `risk_detection_skill(context) → dict` | Function (public skill) | Phát hiện rủi ro (rule-based, no LLM) | — |
| `priority_recommendation_skill(context) → dict` | Function (public skill) | Xếp hạng ưu tiên việc cần làm | `openai.OpenAI`, `core.config.settings` |
| `waiting_response_skill(context) → dict` | Function (public skill) | Phân tích email đang chờ phản hồi | — |
| `deadline_intelligence_skill(context) → dict` | Function (public skill) | Tổng hợp deadlines từ email + calendar | — |
| `productivity_insight_skill(context) → dict` | Function (public skill) | Phân tích hiệu suất làm việc | `openai.OpenAI`, `core.config.settings` |
| `classify_executive_intent(message) → str` | Function (public) | Phân loại câu hỏi executive (keyword-based) | — |
| `_format_priorities(...)` | Function (private) | Format kết quả priorities | — |
| `_format_waiting(...)` | Function (private) | Format kết quả waiting | — |
| `_format_deadlines(...)` | Function (private) | Format kết quả deadlines | — |
| `_format_briefing(...)` | Function (private) | Format kết quả briefing | — |
| `_llm_call(prompt, skill_name, max_tokens) → dict` | Function (private) | Gọi OpenAI chung cho các skill | `openai.OpenAI`, `core.config.settings` |

**Relationships:**
- `gather_context` depends on `app.db.sqlite` (6 functions) + `agents.chat_agent._fetch_upcoming_events` (heavy dependency)
- `_llm_call` creates new `OpenAI` client each call (not singleton)
- All format functions are pure (no dependencies)

#### 5.1.6 `app/agents/evaluation_agent.py` — Evaluation Agent (from test files)

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| Evaluates pipeline output quality | Function | Kiểm tra kết quả pipeline có đạt yêu cầu không | `openai.OpenAI`, `core.config.settings` |

### 5.2 Package: `app/api/v1/`

#### 5.2.1 `app/api/v1/auth.py` — Authentication Router

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `router` | APIRouter | FastAPI router prefix="/auth" | FastAPI |
| `login(request) → RedirectResponse` | GET /auth/login | Redirect đến Google OAuth consent screen | `google_auth_oauthlib.Flow`, `core.config.settings` |
| `auth_callback(request, code, state) → RedirectResponse` | GET /auth/callback | Xử lý OAuth callback, tạo JWT | `google_auth_oauthlib.Flow`, `google.oauth2.id_token`, `core.jwt_auth.create_access_token`, `db.sqlite.create_or_update_user` |
| `get_me(current_user) → dict` | GET /auth/me | Trả về thông tin user hiện tại | `core.jwt_auth.get_current_user` |
| `logout() → JSONResponse` | POST /auth/logout | Xóa access_token cookie | FastAPI |
| `_get_oauth_client_config() → dict` | Function (private) | Đọc OAuth config từ settings hoặc credentials.json | `core.config.settings` |

#### 5.2.2 `app/api/v1/chat.py` — Chat Router

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `router` | APIRouter | FastAPI router | FastAPI |
| Chat endpoint | POST | Xử lý tin nhắn chat từ UI | `agents.chat_agent.chat`, `core.jwt_auth.get_current_user` |
| Confirm endpoint | POST | Xác nhận pending action | Multiple agents & services |
| Decline endpoint | POST | Từ chối pending action | Multiple agents & services |

#### 5.2.3 `app/api/v1/dashboard.py` — Dashboard Router (1291 lines)

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `router` | APIRouter | FastAPI router | FastAPI |
| `EditDraftBody` | Pydantic BaseModel | Schema cho edit draft request | pydantic |
| `SuggestTimeBody` | Pydantic BaseModel | Schema cho suggest time request | pydantic |
| `SendActionBody` | Pydantic BaseModel | Schema cho send action request | pydantic |
| `_generate_email_draft(action_type, context, detected_language) → str` | Function (private) | Sinh email draft qua GPT-4o-mini | `openai.OpenAI`, `core.config.settings` |
| `_find_free_slots(from_dt, n_slots) → list[dict]` | Function (private) | Tìm khung giờ trống | `agents.calendar_agent._check_conflict`, `agents.calendar_agent._get_service` |
| Multiple dashboard endpoints | GET/POST | Pending actions list, email stats, KPI summary, draft management, send actions | `db.sqlite.*`, various agents |

#### 5.2.4 `app/api/v1/webhook.py` — Webhook Router

| Class/Function | Type | Purpose | Collaborators |
| --- | --- | --- | --- |
| `router` | APIRouter | FastAPI router | FastAPI |
| `gmail_webhook(payload) → dict` | POST /webhook/gmail | Nhận email từ Gmail webhook, chạy pipeline | `schemas.email.EmailSchema`, `core.logger.log_event`, `orchestrator.orchestrator.run_pipeline` |

### 5.3 Package: `app/core/`

| File | Purpose | Key Exports |
| --- | --- | --- |
| `config.py` | Application settings từ .env file | `settings` (pydantic Settings object) |
| `auth.py` | Google API service builders | `get_calendar_service()`, `get_gmail_service()` |
| `jwt_auth.py` | JWT creation, verification, middleware | `create_access_token()`, `get_current_user()` |
| `logger.py` | Event logging to DB | `log_event(agent, status, payload)` |

### 5.4 Package: `app/db/`

| File | Purpose |
| --- | --- |
| `sqlite.py` | SQLite database initialization and all CRUD operations (users, emails, pending_actions, logs, etc.) |

**Key Functions (from `app/db/sqlite.py`):**
- `init_db()` — Tạo tất cả các bảng
- `create_or_update_user(...)` — User management
- `get_pending_actions(page, page_size)` — Combined queue
- `list_pending_actions(status, page, page_size)` — Filtered pending actions
- `get_top_important_emails(since, top_n)` — Top emails
- `get_email_statistics_since(since)` — Email statistics
- `get_sent_emails(page, page_size)` — Sent emails
- `get_log_stats()` — Log statistics
- `get_dashboard_summary()` — Dashboard KPIs
- `insert_pending_action(...)` — Create pending action
- `update_pending_action_status(...)` — Update status

### 5.5 Package: `app/orchestrator/`

| File | Purpose | Key Functions |
| --- | --- | --- |
| `orchestrator.py` | Central pipeline coordinator | `run_pipeline(email) → dict` — Routes email through EmailAgent, determines flow type, calls CalendarAgent and NotificationAgent as needed |

### 5.6 Package: `app/schemas/`

| File | Purpose | Key Models |
| --- | --- | --- |
| `email.py` | Email data models | `EmailSchema` (sender, subject, body, timestamp) |
| `chat.py` | Chat-related models | Chat request/response models |

---

## 6. DATABASE DESIGN

### Database Technology

**SQLite** — Lightweight, file-based, no external server required.

Database file: Managed in `app/db/sqlite.py` via Python's `sqlite3` module.

### Entities/Tables

#### Table: `users`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `google_id` | TEXT | UNIQUE NOT NULL |
| `email` | TEXT | UNIQUE NOT NULL |
| `name` | TEXT | |
| `picture_url` | TEXT | |
| `access_token` | TEXT | |
| `refresh_token` | TEXT | |
| `token_expiry` | TEXT | ISO datetime string |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |
| `updated_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### Table: `email_insights` (or similar)

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `sender` | TEXT | NOT NULL |
| `subject` | TEXT | |
| `body` | TEXT | |
| `timestamp` | TEXT | |
| `intent` | TEXT | schedule/reschedule/cancel/inquiry/send_email/reply_email/other |
| `category` | TEXT | Meeting/Work/Personal/... |
| `priority` | TEXT | High/Medium/Low |
| `summary` | TEXT | |
| `action_required` | BOOLEAN | |
| `important_note` | TEXT | |
| `time` | TEXT | ISO datetime |
| `old_time` | TEXT | ISO datetime |
| `location` | TEXT | |
| `attendees` | TEXT | JSON array |
| `confidence` | REAL | 0.0–1.0 |
| `raw_time_text` | TEXT | |
| `detected_language` | TEXT | vi/en/ja/ko/other |
| `sentiment` | TEXT | positive/neutral/negative/urgent |
| `extracted_data_json` | TEXT | Full JSON extracted data |
| `is_read` | BOOLEAN | DEFAULT FALSE |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### Table: `pending_actions`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `source` | TEXT | email_insights/pending_actions |
| `sender` | TEXT | |
| `subject` | TEXT | |
| `action_type` | TEXT | meeting_request/meeting_cancel/reply_required/... |
| `status` | TEXT | pending/draft_ready/waiting_send_confirmation/waiting_external_reply/completed |
| `context_json` | TEXT | JSON context data |
| `draft_response` | TEXT | |
| `calendar_result_json` | TEXT | JSON calendar result |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |
| `updated_at` | TEXT | |

#### Table: `event_logs`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `agent` | TEXT | |
| `status` | TEXT | received/processed/error/... |
| `payload_json` | TEXT | |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### Table: `sent_emails`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `to_email` | TEXT | |
| `subject` | TEXT | |
| `body` | TEXT | |
| `status` | TEXT | sent/error |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP |

### Entity Relationships (ERD)

```
User (1) ──────────── (N) email_insights       [implied via sender email, not FK]
User (1) ──────────── (N) pending_actions       [implied via ownership]
email_insights (1) ──── (1) pending_actions     [source reference]
pending_actions (1) ─── (0..1) sent_emails      [result of action]
email_insights (1) ──── (N) event_logs          [processing logs]
```

**Relationship Types:**
- **User → email_insights:** One-to-Many (một user xử lý nhiều email)
- **email_insights → pending_actions:** One-to-One (mỗi email tạo một pending action nếu cần)
- **pending_actions → sent_emails:** One-to-One (mỗi action khi gửi tạo một sent_email)
- **email_insights → event_logs:** One-to-Many (mỗi email có nhiều log events)

---

## 7. API DESIGN

### API Endpoints

| Method | Endpoint | Description | Auth | Request Body | Response Body |
| --- | --- | --- | --- | --- | --- |
| GET | `/auth/login` | Redirect to Google OAuth | None (Session) | — | 302 Redirect |
| GET | `/auth/callback` | OAuth callback handler | Session state | Query: code, state | 302 Redirect + JWT Cookie |
| GET | `/auth/me` | Current user info | JWT | — | `{id, email, name, picture_url}` |
| POST | `/auth/logout` | Logout | JWT | — | `{status: "logged_out"}` |
| POST | `/api/v1/chat` | Chat message | JWT | `{message: str}` | `{reply: str, action: dict|null}` |
| POST | `/api/v1/chat/confirm/{action_id}` | Confirm pending action | JWT | — | Action result |
| POST | `/api/v1/chat/decline/{action_id}` | Decline pending action | JWT | — | Action result |
| GET | `/api/v1/dashboard/pending-actions` | List pending actions | JWT | Query: page, page_size, status | `{items: [...], total: int}` |
| GET | `/api/v1/dashboard/summary` | Dashboard KPI summary | JWT | — | `{emails_processed, meetings_scheduled, pending_actions, errors}` |
| GET | `/api/v1/dashboard/email-stats` | Email statistics | JWT | Query: since | `{total, meeting, report, ...}` |
| GET | `/api/v1/dashboard/top-emails` | Top important emails | JWT | Query: since, top_n | `[{sender, subject, summary, ...}]` |
| GET | `/api/v1/dashboard/pending-actions/{id}` | Get action detail | JWT | — | Action detail |
| POST | `/api/v1/dashboard/pending-actions/{id}/draft` | Generate email draft | JWT | — | `{draft_response: str}` |
| PUT | `/api/v1/dashboard/pending-actions/{id}/draft` | Edit email draft | JWT | `{draft_response: str}` | `{status: "updated"}` |
| POST | `/api/v1/dashboard/pending-actions/{id}/send` | Send email for action | JWT | `{draft_response?: str}` | `{status: "sent"}` |
| POST | `/api/v1/dashboard/pending-actions/{id}/suggest-time` | Suggest new meeting time | JWT | `{selected_slot: str}` | `{free_slots: [...], suggestion: str}` |
| POST | `/webhook/gmail` | Gmail incoming webhook | None (webhook) | EmailSchema | `{status, flow, data}` |

### Authentication Flow

1. User accesses `/auth/login` → Redirected to Google OAuth consent screen
2. User approves scopes (Calendar, Gmail, userinfo) → Google redirects to `/auth/callback?code=...`
3. Server exchanges code for tokens → Extracts user info from id_token → Creates/updates user in DB → Creates JWT → Sets httpOnly cookie
4. Subsequent API calls → `get_current_user()` dependency verifies JWT from cookie or Authorization header

---

## 8. USER INTERFACE ANALYSIS

### Frontend Framework

**Vanilla HTML/CSS/JS** — Single file `app/chat_ui.html` (4,384 lines)

No framework (React, Vue, Angular) is used. The entire UI is a single-page application built with:
- Custom CSS (Nunito/Quicksand fonts from Google Fonts)
- Vanilla JavaScript with Fetch API for backend communication
- Cookie-based authentication (reads `access_token` cookie)

### Screen Hierarchy

```
Chat UI (/) — Single page with 3 tabs
├── 💬 Chat Tab (chat_tab)
│   ├── Message List (chat_messages)
│   │   ├── User Message Bubble
│   │   ├── Bot Message Bubble
│   │   ├── Action Card (calendar/email actions)
│   │   │   ├── Accept Button
│   │   │   ├── Reject Button
│   │   │   └── Suggest Time Button
│   │   └── Suggested Times Popup (suggest_modal)
│   ├── Chat Input (chat_input)
│   │   ├── Text Input
│   │   └── Send Button
│   └── Suggested Actions (suggested_actions)
│
├── 📋 Dashboard Tab (dashboard_tab)
│   ├── Executive Briefing Widget
│   ├── Pending Actions Widget
│   │   ├── Action Item Card
│   │   │   ├── Accept Button
│   │   │   ├── Reject Button
│   │   │   └── Suggest Time Button
│   │   └── Pagination
│   ├── Email Draft Modal
│   └── Summary KPIs
│
└── ⏳ Waiting Response Widget
    ├── Waiting List
    └── Follow-up Recommendations
```

### Screen Details

#### Screen: Chat Tab
- **Purpose:** Giao tiếp bằng ngôn ngữ tự nhiên với AI để đặt lịch, xem lịch, dời lịch, soạn/gửi email
- **Main UI Components:** Message bubbles (user/bot), action cards, text input, suggested actions bar
- **Related APIs:** `POST /api/v1/chat`

#### Screen: Dashboard Tab
- **Purpose:** Xem tổng quan pending actions, email statistics, executive briefing, rủi ro
- **Main UI Components:** KPI cards, pending actions list, executive briefing widget, waiting response widget
- **Related APIs:** All `/api/v1/dashboard/*` endpoints

---

## 9. IMPORTANT CLASSES

### 9.1 `process_email` in `app/agents/email_agent.py`

**Responsibilities:** Phân tích email đến qua GPT-4o, trích xuất ý định, danh mục, độ ưu tiên, thời gian, sentiment, ngôn ngữ. Đây là entry point đầu tiên của toàn bộ pipeline.

**Main Attributes:** Uses SYSTEM_PROMPT (constant), VALID_* sets

**Main Methods:**
- `process_email(email) → dict` — Main entry, calls GPT-4o, validates result
- `_extract_json(raw) → dict` — Parse GPT response
- `_validate_and_normalise(result) → dict` — Normalize all fields

**Dependencies:** `openai.OpenAI`, `app.core.config.settings`

**Why Important:** This is the **first step of the entire pipeline**. All downstream decisions (calendar actions, notifications) depend on the quality of `email_agent`'s classification. If this fails, everything fails.

---

### 9.2 `run_pipeline` in `app/orchestrator/orchestrator.py`

**Responsibilities:** Central coordinator that routes an incoming email through the complete processing pipeline: EmailAgent → intent routing → CalendarAgent → NotificationAgent. Manages HITL (Human-in-the-Loop) by storing to pending_actions when confidence is low.

**Main Methods:**
- `run_pipeline(email) → dict` — Main orchestrator function

**Dependencies:** All agents, `app.db.sqlite`, `app.schemas`

**Why Important:** This is the **brain of the system** — it decides the flow type (schedule/reschedule/cancel/...) and coordinates all agents. It embodies the core business logic.

---

### 9.3 `chat` in `app/agents/chat_agent.py`

**Responsibilities:** Handles conversational AI interaction. Routes executive questions to ChiefOfStaffAgent, processes regular chat through GPT-4o with structured action extraction (schedule, reschedule, send_email, reply_email, query_calendar).

**Main Methods:**
- `chat(messages) → dict` — Returns `{reply, action}`

**Dependencies:** `openai.OpenAI`, `app.core.auth.get_calendar_service`, `agents.chief_of_staff_agent`

**Why Important:** This is the **user-facing AI** — the primary interface between the user and all system capabilities.

---

### 9.4 `get_current_user` in `app/core/jwt_auth.py`

**Responsibilities:** FastAPI dependency that verifies JWT from cookie or Authorization header. Returns current user dict or raises 401.

**Main Methods:**
- `create_access_token(user_id, email) → str`
- `get_current_user(request) → dict`

**Dependencies:** PyJWT, `core.config.settings`

**Why Important:** **Security gateway** — every protected API endpoint depends on this function. It's the sole authentication/authorization point.

---

### 9.5 `check_calendar_availability` in `app/agents/calendar_agent.py`

**Responsibilities:** Read-only calendar inspection — checks if a time slot is free without creating/modifying any events. Used by the HITL pipeline to inform the user before taking action.

**Main Methods:**
- `check_calendar_availability(email_result) → dict`
- `check_reschedule_availability(email_result) → dict`

**Dependencies:** `googleapiclient`, `core.auth.get_calendar_service`

**Why Important:** Represents the **HITL safety mechanism** — ensures no destructive actions happen without the system knowing the calendar state first.

---

### 9.6 `gather_context` in `app/agents/chief_of_staff_agent.py`

**Responsibilities:** Collects all system-state data needed by ChiefOfStaff skills. Queries the database for pending actions, email statistics, top emails, waiting replies, and calendar for upcoming events.

**Main Methods:**
- `gather_context(last_view) → dict`

**Dependencies:** `app.db.sqlite` (6 functions), `agents.chat_agent._fetch_upcoming_events`

**Why Important:** **Single source of truth aggregator** — every executive intelligence skill depends on this function's output. It bridges database state and calendar state.

---

### 9.7 `send_notification` in `app/agents/notification_agent.py`

**Responsibilities:** Sends email responses based on pipeline results. Builds appropriate email templates (success, conflict, reschedule, error) and sends via Gmail API.

**Main Methods:**
- `send_notification(email_obj, email_result, calendar_result, conflict_result) → dict`

**Dependencies:** `googleapiclient`, `core.auth.get_gmail_service`

**Why Important:** **Closing the loop** — this is how the system communicates results back to external users. Without it, the system is a black box.

---

## 10. USE CASE EXECUTION FLOWS

### 10.1 Flow: Email Processing Pipeline (Schedule)

**Actors:** External Email Sender (trigger), System (automated)

**Trigger:** Gmail webhook receives incoming email → forwarded to `POST /webhook/gmail`

**Steps:**
1. `webhook.py` receives EmailSchema payload
2. `log_event("webhook", "received", payload)` — logs to event_logs table
3. `run_pipeline(payload)` called
4. `email_agent.process_email(email)` — GPT-4o classifies email → returns `{intent, category, priority, time, attendees, ...}`
5. Orchestrator reads `intent` field:
   - `intent == "schedule"` → calls `calendar_agent.check_calendar_availability(email_result)`
   - If free → `calendar_agent.process_schedule(email_result)` → creates Google Calendar event
   - `notification_agent.send_notification(email_obj, email_result, calendar_result)` → sends confirmation email
   - If conflict → stores to pending_actions with status "pending" for HITL

**Services Invoked:**
- Gmail API (receiving email via webhook)
- OpenAI GPT-4o (email classification)
- Google Calendar API (freebusy check + event creation)
- Gmail API (sending notification email)

**Database Operations:**
- INSERT into `event_logs`
- INSERT into `email_insights`
- INSERT into `pending_actions` (if conflict or low confidence)
- INSERT into `sent_emails` (on notification sent)

**Final Result:** Calendar event created and confirmation email sent, OR pending action stored for user review

---

### 10.2 Flow: Login/Authentication

**Actors:** End User

**Trigger:** User accesses `/ui` → gets redirected to login page → clicks "Login with Google"

**Steps:**
1. User clicks login → `GET /auth/login`
2. Server creates OAuth Flow with Google scopes (Calendar, Gmail, userinfo)
3. Server generates state token, stores in session
4. User redirected to Google consent screen
5. User approves → Google redirects to `/auth/callback?code=...&state=...`
6. Server verifies state matches session
7. Server exchanges code for tokens
8. Server decodes id_token → extracts google_id, email, name, picture
9. `db.sqlite.create_or_update_user(...)` — upserts user record
10. `core.jwt_auth.create_access_token(user_id, email)` — creates JWT
11. JWT set as httpOnly cookie → redirect to `/ui`

**Services Invoked:**
- Google OAuth 2.0
- Google People API (userinfo)

**Database Operations:**
- UPSERT into `users`

**Final Result:** User authenticated, JWT cookie set, redirected to Chat UI

---

### 10.3 Flow: AI Chat (Đặt lịch qua Chat)

**Actors:** End User

**Trigger:** User types "Đặt lịch họp với khách hàng Acme lúc 14h mai" in Chat UI

**Steps:**
1. Chat UI sends `POST /api/v1/chat` with `{message: "Đặt lịch..."}`
2. `jwt_auth.get_current_user` verifies JWT
3. `chat_agent.chat(messages)` called
4. GPT-4o processes message with SYSTEM_PROMPT that includes scheduling rules
5. GPT-4o returns: reply text + `<action>{"type":"schedule","event_type":"meeting","title":"Họp với khách hàng Acme","time":"ISO8601","attendees":[...]}</action>`
6. `chat()` extracts action JSON, removes from reply
7. Returns `{reply: "...", action: {...}}`
8. Chat UI displays reply; if action present, UI calls appropriate confirm endpoint
9. Backend creates calendar event + sends notification

**Services Invoked:**
- OpenAI GPT-4o (chat + action extraction)
- Google Calendar API (event creation)
- Gmail API (notification)

---

### 10.4 Flow: Executive Intelligence (Dashboard)

**Actors:** End User

**Trigger:** User switches to Dashboard tab or asks executive question in chat

**Steps:**
1. `chief_of_staff_agent.gather_context(last_view)` collects:
   - pending_actions (combined queue)
   - waiting_external_reply rows
   - email statistics (since last view)
   - top important emails
   - upcoming calendar events
   - dashboard KPIs
2. Skill function called (e.g., `executive_briefing_skill`)
3. GPT-4o-mini generates natural language briefing in Vietnamese
4. Result formatted and returned to UI

**Services Invoked:**
- OpenAI GPT-4o-mini (briefing generation)
- Google Calendar API (upcoming events)

**Database Operations:**
- SELECT from `pending_actions`, `email_insights`, `event_logs`, `sent_emails`

**Final Result:** AI-generated executive briefing displayed in Dashboard

---

### 10.5 Flow: HITL (Human-in-the-Loop) Confirm

**Actors:** End User

**Trigger:** User clicks "Accept" on a pending action in Dashboard or Chat

**Steps:**
1. API receives `POST /api/v1/dashboard/pending-actions/{id}/send`
2. System reads pending action context
3. Generates email draft via GPT-4o-mini (if not already drafted)
4. Executes calendar action (schedule/reschedule/cancel)
5. Sends notification email to original sender
6. Updates pending_action status to "completed"

**Services Invoked:**
- OpenAI GPT-4o-mini (draft generation)
- Google Calendar API
- Gmail API

**Database Operations:**
- UPDATE `pending_actions` status
- INSERT into `sent_emails`

---

## 11. EXTERNAL SERVICES

| Service | Purpose | Integration Points | Files Involved |
| --- | --- | --- | --- |
| **OpenAI GPT-4o** | Email classification, chat response, action extraction | `openai>=1.0.0` | `app/agents/email_agent.py`, `app/agents/chat_agent.py` |
| **OpenAI GPT-4o-mini** | Executive briefing, productivity insights, priority recommendations, email drafts | `openai>=1.0.0` | `app/agents/chief_of_staff_agent.py`, `app/api/v1/dashboard.py` (via `_generate_email_draft`) |
| **Google OAuth 2.0** | User authentication, authorization for Calendar/Gmail | `google-auth-oauthlib>=1.0.0` | `app/api/v1/auth.py`, `app/core/config.py` |
| **Google Calendar API** | Create/update/query calendar events, freebusy checking | `google-api-python-client>=2.0.0` | `app/agents/calendar_agent.py`, `app/agents/chat_agent.py` (via `_fetch_upcoming_events`) |
| **Gmail API** | Send email notifications/replies | `google-api-python-client>=2.0.0` | `app/agents/notification_agent.py` |
| **Gmail Webhook** | Receive incoming emails (push notification from Gmail) | HTTP endpoint | `app/api/v1/webhook.py` |
| **Google People API** | User profile info (name, email, picture) via id_token | `google-auth>=2.0.0` | `app/api/v1/auth.py` |

**No services used:**
- No Redis
- No Vector Database
- No other external APIs beyond Google and OpenAI

---

## 12. DEPLOYMENT ARCHITECTURE

### Deployment Files Detected

| File | Purpose |
| --- | --- |
| `app/requirements.txt` | Python dependencies |
| `.github/workflows/test.yml` | CI/CD: GitHub Actions test pipeline |
| `.env` | Environment variables (OPENAI_API_KEY, Google OAuth config, JWT secret) |
| `.gitignore` | Git ignore rules |
| `pytest.ini` | Pytest configuration |
| `.coveragerc` | Test coverage configuration |

**No Docker files detected** (no Dockerfile, no docker-compose.yml).
**No Nginx configuration detected.**
**No Kubernetes configuration detected.**

### Deployment Architecture Diagram (Inferred)

```
┌──────────────────────────────────────────────┐
│                  CLIENT                       │
│    Browser (Chrome/Firefox/Safari)            │
│    chat_ui.html (Single Page App)             │
└──────────────────────┬───────────────────────┘
                       │ HTTPS
┌──────────────────────▼───────────────────────┐
│              FRONTEND STATIC                  │
│  FastAPI serves app/chat_ui.html              │
│  (or mounted as static file)                  │
└──────────────────────┬───────────────────────┘
                       │ HTTP (same origin)
┌──────────────────────▼───────────────────────┐
│            BACKEND (FastAPI + Uvicorn)         │
│  Port: 8000 (default)                         │
│  app/main.py                                  │
│  ┌──────────────────────────────────────┐    │
│  │ API Routers                          │    │
│  │ /auth/*  /api/v1/*  /webhook/*       │    │
│  └──────────────────────────────────────┘    │
│  ┌──────────────────────────────────────┐    │
│  │ AI Agents (Email, Chat, Calendar,    │    │
│  │           Notification, ChiefOfStaff) │    │
│  └──────────────────────────────────────┘    │
└──────┬──────────────────┬────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌──────────────────┐
│ SQLite DB    │  │ External APIs     │
│ (file-based) │  │ - OpenAI API      │
│ app/*.db     │  │ - Google Calendar │
└──────────────┘  │ - Gmail API       │
                  │ - Google OAuth    │
                  └──────────────────┘
```

### Environment Variables (from `app/core/config.py` and `.env`)

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o/GPT-4o-mini |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_OAUTH_REDIRECT_URI` | OAuth redirect URI |
| `GOOGLE_CREDENTIALS_PATH` | Path to credentials.json |
| `ORGANIZER_EMAIL` | Calendar organizer email |
| `JWT_SECRET` | Secret key for JWT signing |
| `JWT_EXPIRE_MINUTES` | JWT expiration time |
| `DATABASE_PATH` | SQLite database file path |

---

## 13. IMPLEMENTATION STATISTICS

### File Count

| Category | Count |
| --- | --- |
| Python source files (app/) | ~20 |
| Test files | ~12 |
| Configuration files | 5 (.env, .gitignore, pytest.ini, .coveragerc, cookies.txt) |
| Documentation files | 3 (docs/*.md) |
| HTML files | 1 |
| LaTeX thesis files | 5+ |
| GitHub workflow files | 1 |

### Estimated Lines of Code

| File | Approximate LoC |
| --- | --- |
| `app/chat_ui.html` | ~4,384 |
| `app/api/v1/dashboard.py` | ~1,291 |
| `app/agents/chief_of_staff_agent.py` | ~723 |
| `app/agents/calendar_agent.py` | ~594 |
| `app/agents/email_agent.py` | ~337 |
| `app/agents/chat_agent.py` | ~333 |
| `app/agents/notification_agent.py` | ~309 |
| `app/api/v1/auth.py` | ~264 |
| `app/api/v1/chat.py` | ~200+ |
| `app/orchestrator/orchestrator.py` | ~200+ |
| `app/db/sqlite.py` | ~300+ |
| `app/core/*.py` | ~150+ |
| `app/schemas/*.py` | ~50+ |
| `app/main.py` | ~50+ |
| Test files | ~2,000+ |
| **Total (estimated)** | **~11,000+** |

### Language Breakdown

| Language | Files | Approximate % |
| --- | --- | --- |
| Python | ~32 | ~60% |
| HTML/CSS/JavaScript | 1 (chat_ui.html) | ~35% |
| LaTeX | 5+ | ~3% |
| YAML (GitHub Actions) | 1 | ~1% |
| Markdown | 3 | ~1% |

### Counts (Estimated)

| Metric | Count |
| --- | --- |
| Total classes | ~5 (Pydantic models only — the project is functional, not OOP) |
| Total interfaces | 0 (Python uses duck typing, no formal interfaces defined) |
| Total API endpoints | ~16 |
| Total database tables | 5 (users, email_insights, pending_actions, event_logs, sent_emails) |
| Total frontend pages/screens | 1 (chat_ui.html with 3 tabs) |
| Total agent modules | 6 |
| Total test files | ~12 |
| Total OpenAI integration points | 5+ (email_agent, chat_agent, chief_of_staff, dashboard draft, evaluation) |

**Note:** The project is primarily **functional/procedural** — most logic is in module-level functions, not classes. Only Pydantic models and FastAPI routers use classes.

---

## 14. THESIS MATERIALS

### Suitable Architecture Name

**"Layered Architecture with Agent-Based AI Pipeline"** (Kiến trúc phân lớp kết hợp Pipeline AI hướng tác tử)

Alternative: **"Multi-Agent AI Email & Calendar Automation System"**

### Main Packages to Show in UML

1. **Agents Package** (`app/agents/`) — Core AI processing layer with 6 agents
2. **API Package** (`app/api/v1/`) — REST API endpoints
3. **Orchestrator** (`app/orchestrator/`) — Pipeline coordinator
4. **Core Infrastructure** (`app/core/`) — Configuration, auth, JWT
5. **Database Layer** (`app/db/`) — SQLite data access
6. **Domain Schemas** (`app/schemas/`) — Pydantic data models

### Important Classes to Discuss

1. **`process_email`** (email_agent.py) — Email Intelligence, AI classification
2. **`run_pipeline`** (orchestrator.py) — Central coordination, flow routing
3. **`chat`** (chat_agent.py) — Conversational AI with action extraction
4. **`process_schedule`** (calendar_agent.py) — Calendar event creation
5. **`get_current_user`** (jwt_auth.py) — JWT authentication
6. **`gather_context`** (chief_of_staff_agent.py) — Context aggregation
7. **`send_notification`** (notification_agent.py) — Automated email responses
8. **`check_calendar_availability`** (calendar_agent.py) — HITL safety check

### Important Sequence Diagrams to Draw

1. **Email Processing Pipeline (Schedule Flow)**
   ```
   Gmail Webhook → Webhook API → Orchestrator → EmailAgent (GPT-4o)
   → CalendarAgent (freebusy + create event) → NotificationAgent (Gmail send)
   ```

2. **Google OAuth Login Flow**
   ```
   Browser → /auth/login → Google OAuth → /auth/callback → JWT creation → Redirect to UI
   ```

3. **AI Chat with Action Execution**
   ```
   User → Chat UI → /api/v1/chat → ChatAgent (GPT-4o) → Action extraction
   → CalendarAgent → NotificationAgent → Response to UI
   ```

4. **HITL (Human-in-the-Loop) Confirmation**
   ```
   Dashboard → Get pending actions → User reviews → Confirm → CalendarAgent
   → NotificationAgent → Status update
   ```

5. **Executive Intelligence Dashboard**
   ```
   User → Dashboard → ChiefOfStaff gather_context → Skills (Briefing/Risk/Priority)
   → GPT-4o-mini → Formatted response
   ```

### Important UI Screens to Include

1. **Chat UI Tab** — Main conversational interface with message bubbles, action cards
2. **Dashboard Tab** — Executive briefing, pending actions, KPIs
3. **Login Page** — Google OAuth login button
4. **Pending Action Detail** — Email draft modal, accept/reject/suggest buttons

### Important Database Entities to Include

1. **users** — OAuth user accounts with token storage
2. **email_insights** — AI-processed email intelligence (intent, category, priority, sentiment)
3. **pending_actions** — HITL work queue (meeting requests, replies, reschedules)
4. **event_logs** — Audit trail for pipeline processing
5. ERD diagram showing relationships

### Key Implementation Technologies

| Technology | Role |
| --- | --- |
| **FastAPI** | Backend REST API framework |
| **OpenAI GPT-4o** | Primary AI engine (email classification, chat, action extraction) |
| **OpenAI GPT-4o-mini** | Lightweight AI (drafts, briefings, insights) |
| **Google Calendar API** | Calendar event CRUD, freebusy checking |
| **Gmail API** | Sending automated email responses |
| **Google OAuth 2.0** | Authentication + authorization for Google services |
| **SQLite** | Embedded database (no external DB server) |
| **Pydantic v2** | Data validation and serialization |
| **PyJWT / python-jose** | JWT-based session management |
| **Vanilla HTML/CSS/JS** | Frontend (no framework dependency) |

---

*Report generated from actual source code analysis of `email_scheduler_ai` repository.*
*Commit: 461cd177904178477e684918bf663b8146fd448b*
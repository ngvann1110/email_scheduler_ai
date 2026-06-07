# Email Scheduler AI — Feature Inventory

> **Repository:** `email_scheduler_ai` | **Commit:** `c9e73fcd` | **Date:** 2026-06-08

---

## 1. Feature Catalog

### Status Legend

| Icon | Status | Description |
|------|--------|-------------|
| ✅ | Implemented | Fully functional with code evidence |
| ⚠️ | Partial | Core logic exists but has known gaps |
| 🔬 | Experimental | Implemented but not thoroughly tested |
| ❌ | Not implemented | No code evidence found |

---

## 2. Core Features

### A. Email Ingestion

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Gmail Inbox Polling | ✅ | `main.py:46` — `asyncio.create_task(poll_gmail())` | `core/gmail_poller.py:poll_gmail()` (line 59), `_parse_message()` (line 18), `_mark_as_read()` (line 47) | N/A |
| Gmail Webhook Ingestion | ✅ | `api/v1/webhook.py:9` — `POST /webhook/gmail` | `api/v1/webhook.py:gmail_webhook()` | N/A |
| Raw Email Parsing | ✅ | Pipeline | `core/gmail_poller.py:18-44` — `_parse_message()` | N/A |
| Sender Self-Exclusion | ✅ | Poller query | `core/gmail_poller.py:74` — `-from:{ORGANIZER_EMAIL}` | N/A |
| Max 10 Results Per Poll | ✅ | Poller query | `core/gmail_poller.py:72` — `maxResults=10` | N/A |
| 24-Hour Poll Window | ✅ | Poller query | `core/gmail_poller.py:74` — `newer_than:1d` | N/A |

### B. Spam Filtering

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Sender Keyword Check | ✅ | `is_spam()` | `agents/spam_filter.py:71-79` — 13 patterns | N/A |
| Subject Keyword Check | ✅ | `is_spam()` | `agents/spam_filter.py:82-89` — 24 patterns | N/A |
| Body Keyword Check | ✅ | `is_spam()` | `agents/spam_filter.py:92-100` — 7 patterns | N/A |
| Multi-language Spam | ✅ | Keywords include Vietnamese | `agents/spam_filter.py:87-89` — "khuyến mãi", "giảm giá", etc. | N/A |
| ML-based Spam | ❌ | N/A | No ML model or training data found | N/A |

### C. AI Intent Classification

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Schedule Intent | ✅ | `process_email()` | `agents/email_agent.py:73-121` — GPT-4o | N/A |
| Cancel Intent | ✅ | `process_email()` | `agents/email_agent.py:73-121` — GPT-4o | N/A |
| Reschedule Intent | ✅ | `process_email()` | `agents/email_agent.py:73-121` — GPT-4o | N/A |
| Inquiry Intent | ✅ | `process_email()` | `agents/email_agent.py:73-121` — GPT-4o | N/A |
| Other Intent | ✅ | `process_email()` | `agents/email_agent.py:73-121` + `_fallback()` (line 57) | N/A |
| Structured JSON Output | ✅ | GPT-4o config | `agents/email_agent.py:92-94` — `response_format={"type": "json_object"}` | N/A |
| Deterministic Classification | ✅ | GPT-4o config | `agents/email_agent.py:95` — `temperature=0` | N/A |
| Natural Language Time Parsing | ✅ | System prompt | `agents/email_agent.py:15-48` — SYSTEM_PROMPT in Vietnamese | N/A |
| Fallback on Error | ✅ | Try/except | `agents/email_agent.py:117-119` — `_fallback(reason)` | N/A |

### D. Google Calendar Operations

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Event Creation | ✅ | Pipeline schedule | `agents/calendar_agent.py:31-49` — `_create_event()` | Action cards in `chat_ui.html` |
| Event Cancellation | ✅ | Pipeline cancel | `agents/calendar_agent.py:123-194` — `process_cancel()` | Cancel links in emails |
| Event Rescheduling | ✅ | Pipeline reschedule | `agents/calendar_agent.py:197-301` — `process_reschedule()` | Reschedule links in emails |
| Freebusy Conflict Check | ✅ | `_check_conflict()` | `agents/calendar_agent.py:18-28` | N/A |
| Event Discovery (±1h) | ✅ | `_find_events_by_time()` | `agents/calendar_agent.py:52-64` | N/A |
| Event Reminders | ✅ | `_create_event()` | `agents/calendar_agent.py:39-45` — email 24h, popup 30m | N/A |
| Send Updates to Attendees | ✅ | All calendar ops | `sendUpdates="all"` parameter | N/A |
| Multiple Attendee Support | ✅ | `_create_event()` | `agents/calendar_agent.py:38` — filter attendees for "@" | N/A |
| Location Field | ✅ | `_create_event()` | `agents/calendar_agent.py:37` — optional location | N/A |
| Timezone (Asia/Ho_Chi_Minh) | ✅ | All calendar ops | `agents/calendar_agent.py:15` — `TIMEZONE = "Asia/Ho_Chi_Minh"` | N/A |
| Calendar Query (Chat) | ✅ | `_fetch_upcoming_events()` | `agents/chat_agent.py:68-99` — 7-day, 20 max | Chat UI display |
| Single Calendar Only | ⚠️ | Hardcoded | `CALENDAR_ID = "primary"` in 3 files | N/A |
| Recurring Meetings | ❌ | N/A | No `recurrence` field | N/A |
| Fixed 60-min Duration | ⚠️ | Hardcoded | `DEFAULT_DURATION_MINUTES = 60` (line 12) | N/A |

### E. Conflict Resolution

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Conflict Detection | ✅ | Schedule + Reschedule | `agents/calendar_agent.py:18-28` — `_check_conflict()` | N/A |
| Alternative Slot Finder | ✅ | After conflict | `agents/conflict_agent.py:93-154` — `find_alternatives()` | N/A |
| Working Hours (8-18h) | ✅ | Hardcoded | `agents/conflict_agent.py:26-27` — `WORK_START_HOUR=8`, `WORK_END_HOUR=18` | N/A |
| 30-Min Slot Increments | ✅ | `_candidate_slots()` | `agents/conflict_agent.py:60-90` — generator | N/A |
| Max 7 Days Lookahead | ✅ | `_candidate_slots()` | `agents/conflict_agent.py:68` — `MAX_DAYS_AHEAD = 7` | N/A |
| Max 3 Suggestions | ✅ | `find_alternatives()` | `agents/conflict_agent.py:113-136` — limit 3 | N/A |
| Vietnamese Day Labels | ✅ | `_label()` | `agents/conflict_agent.py:28-30` | N/A |
| Duplicate Auth | ⚠️ | `_get_service()` | `agents/conflict_agent.py:33-46` — duplicate of `core/auth.py` | N/A |

### F. Notification Emails

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Success Email | ✅ | `send_notification()` | `agents/notification_agent.py:70-113` — `_build_success_email()` | N/A |
| Conflict Email | ✅ | `send_notification()` | `agents/notification_agent.py:118-159` — `_build_conflict_email()` | N/A |
| Cancel Email | ✅ | `send_notification()` | `agents/notification_agent.py:165-187` — `_build_cancel_email()` | N/A |
| Cancel Not Found Email | ✅ | `send_notification()` | `agents/notification_agent.py:192-215` — `_build_cancel_not_found_email()` | N/A |
| Reschedule Email | ✅ | `send_notification()` | `agents/notification_agent.py:220-248` — `_build_reschedule_email()` | N/A |
| Reschedule Not Found Email | ✅ | `send_notification()` | `agents/notification_agent.py:254-270` — `_build_reschedule_not_found_email()` | N/A |
| Error Email | ✅ | `send_notification()` | `agents/notification_agent.py:225-254` — `_build_error_email()` (not directly called, referenced) | N/A |
| Gmail API Send | ✅ | `_send()` | `agents/notification_agent.py:265-272` — `users().messages().send()` | N/A |
| UTF-8 Vietnamese Content | ✅ | All templates | Unicode box-drawing + Vietnamese text | N/A |
| Email Threading | ❌ | N/A | No `In-Reply-To` or `References` headers | N/A |

### G. Evaluation & Retry

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| LLM Quality Evaluation | ✅ | `evaluate_and_retry()` | `agents/chat_agent.py:123-148` — `evaluate_email()` | N/A |
| Retry Loop (3 attempts) | ✅ | `evaluate_and_retry()` | `agents/evaluation_agent.py:32-116` | N/A |
| 2-Second Retry Delay | ✅ | `evaluate_and_retry()` | `agents/evaluation_agent.py:97` — `await asyncio.sleep(2)` | N/A |
| Retry Logging | ✅ | `evaluate_and_retry()` | `agents/evaluation_agent.py:91-99` — `log_event()` | Dashboard log viewer |
| Fallback on Eval Failure | ✅ | `_evaluate_result()` | `agents/evaluation_agent.py:25-30` — returns `"acceptable"` | N/A |

---

## 3. Authentication & Authorization

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Google OAuth Login | ✅ | `GET /auth/login` | `api/v1/auth.py:73-107` — `google_auth_url()` | Login button in `chat_ui.html` |
| OAuth Callback | ✅ | `GET /auth/callback` | `api/v1/auth.py:110-223` — `callback()` | Redirect handling |
| PKCE (SHA256) | ✅ | Login flow | `api/v1/auth.py:74-81` — code verifier + challenge | N/A |
| Anti-CSRF State | ✅ | Login/Callback | `api/v1/auth.py:112-116` — state cookie validation | N/A |
| JWT Creation (HS256) | ✅ | After OAuth | `core/jwt_auth.py:16-27` — `create_token()` | N/A |
| JWT Validation | ✅ | Protected endpoints | `core/jwt_auth.py:29-46` — `decode_token()` | N/A |
| HttpOnly Cookie | ✅ | Callback | `api/v1/auth.py:214-219` — `httponly=True` | Reads via `credentials: "include"` |
| Current User Dependency | ✅ | FastAPI injection | `core/jwt_auth.py:57-78` — `get_current_user()` | N/A |
| Logout | ✅ | `POST /auth/logout` | `api/v1/auth.py:241-252` — clears cookie | Logout button |
| Token-Based Confirm Links | ✅ | 6 endpoints | `api/v1/chat.py:458-639` | Email links |
| Token Revocation | ❌ | N/A | No token blacklist | N/A |
| Rate Limiting | ❌ | N/A | No rate limit middleware | N/A |
| User Roles/Admin | ❌ | N/A | All users equal | N/A |

---

## 4. Chat UI

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Google Login Screen | ✅ | Page load | N/A | `chat_ui.html` — login button |
| Chat Message Input | ✅ | After login | `POST /chat` | Text input + send button |
| Message History Display | ✅ | After login | Stored in frontend memory | Scrollable message area |
| Action Cards | ✅ | After schedule action | Action in response | Rendered from `{reply, action}` |
| Meet Confirmation | ✅ | Action click | `GET /chat/confirm/{token}` | Confirm button |
| Meet Decline | ✅ | Action click | `GET /chat/decline/{token}` | Decline button |
| Reschedule Confirm | ✅ | Action click | `GET /chat/reschedule/confirm/{token}` | Confirm button |
| Reschedule Decline | ✅ | Action click | `GET /chat/reschedule/decline/{token}` | Decline button |
| Cancel Confirm | ✅ | Action click | `GET /chat/cancel/confirm/{token}` | Confirm button |
| Loading States | ✅ | During API calls | N/A | Loading indicator |
| Dashboard Stats | ✅ | After login | `GET /dashboard/stats` | Stats cards |
| Dashboard Logs | ✅ | After login | `GET /dashboard/logs` | Log table |
| Session Verification | ✅ | After login | `GET /auth/me` | Cookie check |
| Real-Time Updates (WS/SSE) | ❌ | N/A | No WebSocket endpoints | N/A |

---

## 5. Dashboard

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| System Stats | ✅ | `GET /dashboard/stats` | `api/v1/chat.py:693` + `db/sqlite.py:95-118` | Stats cards |
| Event Log Viewer | ✅ | `GET /dashboard/logs` | `api/v1/chat.py:715` + `db/sqlite.py:55-64` | Log table |
| Log Pagination | ✅ | Query params | `db/sqlite.py:55` — `SELECT ... LIMIT ? OFFSET ?` | Pagination UI |
| User Data Isolation | ❌ | N/A | All users see all data | N/A |

---

## 6. Database

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| SQLite Storage | ✅ | Startup | `db/sqlite.py:init_db()` (line 68) | N/A |
| Event Logging | ✅ | Throughout pipeline | `core/logger.py:log_event()` | Dashboard log viewer |
| Pending Invite Storage | ✅ | After event create | `db/sqlite.py:insert_pending_invite()` | N/A |
| Pending Cancel Storage | ✅ | After cancel action | `db/sqlite.py` — `insert_pending_cancel()` | N/A |
| Pending Reschedule Storage | ✅ | After reschedule action | `db/sqlite.py` — `insert_pending_reschedule()` | N/A |
| User Account Storage | ✅ | After OAuth | `db/sqlite.py:create_user()` | N/A |
| Pending Action Cleanup | ✅ | On resolution | `db/sqlite.py:delete_pending_*()` | N/A |
| Database Migrations | ❌ | N/A | Only `CREATE TABLE IF NOT EXISTS` | N/A |
| ORM / Query Builder | ❌ | N/A | Raw SQL via `sqlite3` | N/A |

---

## 7. Infrastructure

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Environment Config | ✅ | `.env` + `config.py` | `core/config.py:Settings` (Pydantic BaseSettings) | N/A |
| CORS Middleware | ✅ | `main.py:20-27` | `CORSMiddleware` with `allow_origins=["*"]` | Enables cross-origin fetch |
| Health Check | ✅ | `GET /health` | `main.py:61-62` — `{"status": "ok"}` | N/A |
| Background Task (Poller) | ✅ | `startup` event | `main.py:46` — `asyncio.create_task(poll_gmail())` | N/A |
| Graceful Shutdown | ❌ | N/A | No `shutdown` event handler | N/A |
| CI/CD Pipeline | ✅ | `.github/workflows/test.yml` | GitHub Actions, pytest, Codecov | N/A |

---

## 8. Testing

| Feature | Status | Entry Point | Backend Components | Frontend Components |
|---------|--------|-------------|-------------------|-------------------|
| Unit Tests (Agent) | ✅ | 12 test files | `tests/unit/test_*_agent.py` — 178 tests total | N/A |
| Integration Tests | ✅ | 5 test files | `tests/integration/test_api_*.py` | N/A |
| E2E Tests | ✅ | 1 test file | `tests/e2e/test_full_pipeline.py` | N/A |
| Frontend Tests | ❌ | `tests/frontend/test_chat_ui.py` | Empty file (0 bytes) | N/A |
| Coverage Reporting | ✅ | `.coveragerc` + CI | `pytest --cov=app` + Codecov upload | N/A |
| Async Test Support | ✅ | `pytest.ini` | `asyncio_mode = auto` | N/A |
| Shared Fixtures | ✅ | `conftest.py` | 430 lines — mock services, test data | N/A |
| Marker Organization | ✅ | `pytest.ini` | unit/integration/e2e/frontend markers | N/A |

---

## 9. Endpoint Reference

| # | Route | Method | Auth | Purpose | Source | Status |
|---|-------|--------|------|---------|--------|--------|
| 1 | `/health` | GET | None | Health check | `main.py:61` | ✅ |
| 2 | `/ui` | GET | None | Chat UI | `main.py:57` | ✅ |
| 3 | `/auth/login` | GET | None | OAuth redirect | `auth.py:73` | ✅ |
| 4 | `/auth/callback` | GET | State | OAuth callback | `auth.py:110` | ✅ |
| 5 | `/auth/me` | GET | JWT | User info | `auth.py:226` | ✅ |
| 6 | `/auth/logout` | POST | None | Clear cookie | `auth.py:241` | ✅ |
| 7 | `/chat` | POST | JWT | Interactive | `chat.py:339` | ✅ |
| 8 | `/chat/confirm/{token}` | GET | Token | Confirm invite | `chat.py:458` | ✅ |
| 9 | `/chat/decline/{token}` | GET | Token | Decline invite | `chat.py:497` | ✅ |
| 10 | `/chat/reschedule/confirm/{token}` | GET | Token | Confirm reschedule | `chat.py:520` | ✅ |
| 11 | `/chat/reschedule/decline/{token}` | GET | Token | Decline reschedule | `chat.py:594` | ✅ |
| 12 | `/chat/cancel/confirm/{token}` | GET | Token | Confirm cancel | `chat.py:639` | ✅ |
| 13 | `/dashboard/stats` | GET | JWT | Statistics | `chat.py:693` | ✅ |
| 14 | `/dashboard/logs` | GET | JWT | Logs | `chat.py:715` | ✅ |
| 15 | `/webhook/gmail` | POST | None | Gmail push | `webhook.py:9` | ✅ |

---

## 10. Agent Reference

| Agent | File | Primary Function | LLM | External API | Status |
|-------|------|-----------------|-----|-------------|--------|
| Spam Filter | `agents/spam_filter.py` | `is_spam()` | No | No | ✅ |
| Email Agent | `agents/email_agent.py` | `process_email()` | GPT-4o | No | ✅ |
| Calendar Agent | `agents/calendar_agent.py` | `process_schedule/cancel/reschedule()` | No | Calendar v3 | ✅ |
| Conflict Agent | `agents/conflict_agent.py` | `find_alternatives()` | No | Calendar v3 | ✅ (duplicate auth) |
| Chat Agent | `agents/chat_agent.py` | `chat()`, `evaluate_email()` | GPT-4o | Calendar v3 (query) | ✅ |
| Notification Agent | `agents/notification_agent.py` | `send_notification()`, `send_reply()` | No | Gmail v1 | ✅ |
| Evaluation Agent | `agents/evaluation_agent.py` | `evaluate_and_retry()` | Via chat_agent | No | ✅ |

---

## 11. Summary

| Category | Total | Implemented | Partial | Not Implemented |
|----------|-------|-------------|---------|-----------------|
| Email Ingestion | 6 | 6 | 0 | 0 |
| Spam Filtering | 5 | 4 | 0 | 1 |
| AI Intent Classification | 8 | 8 | 0 | 0 |
| Calendar Operations | 15 | 13 | 2 | 1 |
| Conflict Resolution | 8 | 7 | 1 | 0 |
| Notification Emails | 10 | 9 | 0 | 1 |
| Evaluation & Retry | 5 | 5 | 0 | 0 |
| Authentication | 12 | 9 | 0 | 3 |
| Chat UI | 14 | 13 | 0 | 1 |
| Dashboard | 4 | 3 | 0 | 1 |
| Database | 9 | 7 | 0 | 2 |
| Infrastructure | 6 | 5 | 0 | 1 |
| Testing | 8 | 7 | 0 | 1 |
| **TOTAL** | **115** | **101** | **3** | **11** |

**Implementation rate: 87.8% (101/115)**

---

*End of Feature Inventory*
# Email Scheduler AI — Architecture Overview

> **Repository:** `email_scheduler_ai` | **Commit:** `c9e73fcd` | **Date:** 2026-06-08

---

## 1. High-Level Architecture

### System Context

```mermaid
C4Context
    title System Context Diagram — Email Scheduler AI

    Person(email_user, "Email User", "Sends meeting requests via email")
    Person(chat_user, "Chat User", "Schedules via web chat UI")
    Person(invitee, "Meeting Invitee", "Confirms/declines meetings via link")

    System(email_scheduler, "Email Scheduler AI", "Automated meeting scheduling\nwith AI + Google Calendar")

    System_Ext(gmail, "Google Gmail", "Email API v1")
    System_Ext(calendar, "Google Calendar", "Calendar API v3")
    System_Ext(openai, "OpenAI GPT-4o", "LLM for intent classification + chat")

    Rel(email_user, "Sends email to", gmail, "SMTP")
    Rel(gmail, "Pushes notification", email_scheduler, "Webhook / Poll")
    Rel(email_scheduler, "Reads inbox", gmail, "Gmail API")
    Rel(email_scheduler, "Classifies + chats", openai, "REST API")
    Rel(email_scheduler, "Manages events", calendar, "Calendar API")
    Rel(chat_user, "Uses", email_scheduler, "HTTPS /chat")
    Rel(invitee, "Clicks link", email_scheduler, "HTTPS /chat/{action}/{token}")
    Rel(email_scheduler, "Sends replies", gmail, "Gmail API")
```

### Container Diagram

```mermaid
C4Container
    title Container Diagram

    Person(user, "User", "Email or Chat user")

    Container_Boundary(app, "Email Scheduler AI Application") {
        Container(api, "FastAPI Server", "Python 3.10+", "Serves REST API, serves chat UI, runs background poller")
        ContainerDb(db, "SQLite Database", "File DB", "Stores system_logs, pending actions, users")
        Container(spa, "Chat SPA", "Vanilla HTML/CSS/JS", "Single-file frontend served at /ui")
    }

    System_Ext(gmail, "Google Gmail API v1")
    System_Ext(calendar, "Google Calendar API v3")
    System_Ext(openai, "OpenAI GPT-4o")

    Rel(user, "HTTPS", api, "REST + UI")
    Rel(api, "Read/Send", gmail, "OAuth 2.0")
    Rel(api, "CRUD", calendar, "OAuth 2.0")
    Rel(api, "Completion", openai, "API Key")
    Rel(api, "Read/Write", db, "sqlite3")
    Rel(spa, "fetch()", api, "Cookies")
```

### Component Diagram

```mermaid
graph TB
    subgraph External["External Services"]
        OpenAI["OpenAI GPT-4o"]
        Gmail["Google Gmail API v1"]
        Calendar["Google Calendar API v3"]
    end

    subgraph FastAPI["FastAPI Server (app/main.py)"]
        direction TB
        AuthAPI["Auth API v1<br/>(/auth/*)"]
        ChatAPI["Chat API v1<br/>(/chat/*)"]
        WebhookAPI["Webhook API v1<br/>(/webhook/*)"]
        StaticRoutes["Static Routes<br/>(/ui, /health)"]
    end

    subgraph Core["Core Infrastructure (app/core/)"]
        Config["Config<br/>(config.py)"]
        AuthProvider["Google Auth<br/>(auth.py)"]
        JWT["JWT Auth<br/>(jwt_auth.py)"]
        Logger["Logger<br/>(logger.py)"]
        Poller["Gmail Poller<br/>(gmail_poller.py)"]
    end

    subgraph Agents["AI Agents (app/agents/)"]
        SpamFilter["Spam Filter<br/>(spam_filter.py)"]
        EmailAgent["Email Agent<br/>(email_agent.py)"]
        CalendarAgent["Calendar Agent<br/>(calendar_agent.py)"]
        ConflictAgent["Conflict Agent<br/>(conflict_agent.py)"]
        ChatAgent["Chat Agent<br/>(chat_agent.py)"]
        NotificationAgent["Notification Agent<br/>(notification_agent.py)"]
        EvaluationAgent["Evaluation Agent<br/>(evaluation_agent.py)"]
    end

    subgraph Orchestration["Orchestration (app/orchestrator/)"]
        Orchestrator["Orchestrator<br/>(orchestrator.py)"]
    end

    subgraph Storage["Storage (app/db/)"]
        SQLite["SQLite<br/>(sqlite.py)"]
    end

    FastAPI --> Config
    AuthAPI --> AuthProvider
    AuthAPI --> JWT
    ChatAPI --> JWT
    ChatAPI --> ChatAgent
    WebhookAPI --> Poller

    Poller --> Gmail
    Poller --> SpamFilter
    Poller --> EmailAgent
    Poller --> EvaluationAgent
    Poller --> Orchestrator
    Poller --> Logger

    EmailAgent --> OpenAI
    Orchestrator --> CalendarAgent
    Orchestrator --> ConflictAgent
    Orchestrator --> NotificationAgent

    CalendarAgent --> Calendar
    ConflictAgent --> Calendar
    NotificationAgent --> Gmail
    ChatAgent --> OpenAI
    ChatAgent --> Calendar

    Logger --> SQLite
    ChatAPI --> SQLite
    Orchestrator --> SQLite

    EvaluationAgent --> ChatAgent
```

---

## 2. System Architecture (Layered)

```
┌─────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                        │
│  ┌──────────────────────┐  ┌──────────────────────────────┐ │
│  │   chat_ui.html       │  │  REST API Endpoints          │ │
│  │   SPA (1623 lines)   │  │  15 routes across 4 groups   │ │
│  └──────────────────────┘  └──────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                    APPLICATION LAYER                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Orchestrator (orchestrator.py)          │   │
│  │         Routes email intents to agent pipelines      │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐    │
│  │Spam  │ │Email │ │Calen-│ │Conf- │ │Chat  │ │Notif-│    │
│  │Filter│ │Agent │ │dar   │ │lict  │ │Agent │ │ication│   │
│  │      │ │      │ │Agent │ │Agent │ │      │ │Agent │    │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘    │
│                    ┌──────────┐                             │
│                    │Evaluation│                             │
│                    │  Agent   │                             │
│                    └──────────┘                             │
├─────────────────────────────────────────────────────────────┤
│                    INFRASTRUCTURE LAYER                      │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐ │
│  │Config  │ │Google  │ │JWT     │ │Logger  │ │Gmail     │ │
│  │Settings│ │Auth    │ │Auth    │ │        │ │Poller    │ │
│  └────────┘ └────────┘ └────────┘ └────────┘ └──────────┘ │
├─────────────────────────────────────────────────────────────┤
│                    DATA LAYER                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SQLite Database (sqlite.py)              │   │
│  │  system_logs | pending_invites | pending_cancels      │   │
│  │  pending_reschedules | users                          │   │
│  └──────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    EXTERNAL SERVICES                         │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │ OpenAI   │  │ Google Gmail │  │ Google Calendar    │    │
│  │ GPT-4o   │  │ API v1       │  │ API v3             │    │
│  └──────────┘  └──────────────┘  └────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Data Flow Diagrams

### Flow 1: Login (Google OAuth)

```mermaid
sequenceDiagram
    participant Browser
    participant FastAPI
    participant Google
    participant SQLite

    Browser->>FastAPI: GET /auth/login
    FastAPI->>FastAPI: Generate PKCE (code_verifier, code_challenge)
    FastAPI->>FastAPI: Store state + code_verifier in cookie
    FastAPI-->>Browser: 302 Redirect to Google consent

    Browser->>Google: User authenticates
    Google-->>Browser: 302 Redirect to /auth/callback?code=...&state=...

    Browser->>FastAPI: GET /auth/callback?code=...&state=...
    FastAPI->>FastAPI: Validate state cookie (anti-CSRF)
    FastAPI->>Google: Exchange code for tokens (fetch_token)
    Google-->>FastAPI: {access_token, refresh_token, ...}
    FastAPI->>Google: GET userinfo
    Google-->>FastAPI: {sub, email, name, picture}
    FastAPI->>SQLite: UPSERT user (get_user_by_email + create_user)
    FastAPI->>FastAPI: create_token(user_id, email, name, picture) → JWT
    FastAPI->>FastAPI: Set HttpOnly cookie "access_token"
    FastAPI-->>Browser: 302 Redirect to /ui

    Browser->>FastAPI: GET /ui
    FastAPI-->>Browser: chat_ui.html
    Browser->>Browser: JS detects access_token cookie → shows chat
    Browser->>FastAPI: GET /auth/me
    FastAPI->>FastAPI: get_current_user() → validate JWT
    FastAPI-->>Browser: {user_id, email, name, picture} → 200
```

**Source files:**
- `app/api/v1/auth.py` — routes 73–252
- `app/core/jwt_auth.py` — token create/decode/validate
- `app/db/sqlite.py` — user CRUD
- `app/core/config.py` — Google OAuth config
- `app/chat_ui.html` — frontend state handling

### Flow 2: Schedule Meeting (Email Path)

```mermaid
sequenceDiagram
    participant Sender as Email User
    participant Gmail as Google Gmail
    participant Poller as Gmail Poller
    participant Spam as Spam Filter
    participant EmailA as Email Agent
    participant Orchestrator
    participant CalA as Calendar Agent
    participant Calendar as Google Calendar
    participant EvalA as Evaluation Agent
    participant NotifA as Notification Agent
    participant SQLite

    Sender->>Gmail: Sends email: "Muốn đặt lịch 14h thứ Hai 28/04"

    Note over Poller: Wakes every GMAIL_POLL_INTERVAL_SECONDS
    Poller->>Gmail: users.messages.list(unread, 1d, exclude self)
    Gmail-->>Poller: [msg_id_1, msg_id_2, ...] (max 10)

    loop For each message
        Poller->>Gmail: users.messages.get(format="raw")
        Gmail-->>Poller: Raw RFC 2822 message
        Poller->>Poller: _parse_message() → email.message_from_bytes()
        Poller->>Poller: EmailSchema(sender, subject, body, timestamp)

        Poller->>Spam: is_spam(email)
        alt SPAM
            Spam-->>Poller: (True, reason)
            Poller->>Gmail: _mark_as_read()
            Poller->>SQLite: log_event("spam_filter", "spam", payload)
            Note over Poller: Skip, next message
        else NOT SPAM
            Spam-->>Poller: (False, "")
            Poller->>Gmail: _mark_as_read() (immediately)

            Poller->>EvalA: evaluate_and_retry(run_pipeline, email)

            loop Up to 3 attempts
                EvalA->>Orchestrator: run_pipeline(email)
                Orchestrator->>EmailA: process_email(email)
                EmailA->>OpenAI: GPT-4o completion (SYSTEM_PROMPT + email)
                OpenAI-->>EmailA: JSON {intent, summary, time, attendees, ...}
                EmailA-->>Orchestrator: email_result

                alt intent = "schedule"
                    Orchestrator->>CalA: process_schedule(email_result)
                    CalA->>Calendar: freebusy.query(timeMin, timeMax)
                    Calendar-->>CalA: {busy: []} or [{start, end}]
                    alt slot free
                        CalA->>Calendar: events.insert(event)
                        Calendar-->>CalA: {id, htmlLink, ...}
                        CalA-->>Orchestrator: {status: "created", event_id, ...}
                    else slot busy
                        CalA-->>Orchestrator: {status: "conflict", busy_slots, ...}
                        Orchestrator->>ConflictA: find_alternatives(time)
                        ConflictA->>Calendar: freebusy query loop
                        Calendar-->>ConflictA: Busy/free results
                        ConflictA-->>Orchestrator: {status: "found", suggestions: [...]}
                    end

                    Orchestrator->>NotifA: send_notification(email, email_result, calendar_result, conflict_result)
                    alt success
                        NotifA->>NotifA: _build_success_email()
                    else conflict
                        NotifA->>NotifA: _build_conflict_email()
                    end
                    NotifA->>Gmail: users().messages().send(raw=base64_email)
                    Gmail-->>NotifA: {id, threadId}

                else intent = "cancel"
                    Orchestrator->>CalA: process_cancel(email_result)
                    CalA->>Calendar: events.list(timeMin, timeMax)
                    Calendar-->>CalA: [events in ±1h window]
                    CalA->>Calendar: events.delete(eventId)
                    Calendar-->>CalA: 204 No Content
                    Orchestrator->>NotifA: send_notification(...)

                else intent = "reschedule"
                    Orchestrator->>CalA: process_reschedule(email_result)
                    CalA->>Calendar: Find old event + check new slot free
                    CalA->>Calendar: events.update(eventId, body)
                    Calendar-->>CalA: {updated event}
                    Orchestrator->>NotifA: send_notification(...)

                else intent = "inquiry"
                    Orchestrator->>CalA: Query calendar events in range
                    Orchestrator->>NotifA: send_reply(email, result)

                else intent = "other"
                    Note over Orchestrator: No action
                end

                Orchestrator-->>EvalA: pipeline_result
                EvalA->>ChatA: evaluate_email(pipeline_result)
                ChatA->>OpenAI: GPT-4o: "Is this response acceptable?"
                OpenAI-->>ChatA: {acceptable: true/false, reason}
                ChatA-->>EvalA: {acceptable, reason}
                EvalA->>SQLite: log_event("evaluation_agent", status, payload)

                alt acceptable
                    EvalA-->>Poller: final_result
                    Note over EvalA: Break retry loop
                else not acceptable (retries remaining)
                    Note over EvalA: Wait 2s, retry
                end
            end

            Poller->>SQLite: log_event("gmail_poller", "processed", payload)
        end
    end

    Note over Poller: Sleep for GMAIL_POLL_INTERVAL_SECONDS, repeat
```

**Source files:**
- `app/core/gmail_poller.py` — polling + message parsing
- `app/agents/spam_filter.py` — spam detection
- `app/agents/email_agent.py` — GPT-4o intent classification
- `app/agents/calendar_agent.py` — Calendar CRUD
- `app/agents/conflict_agent.py` — alternative slots
- `app/agents/notification_agent.py` — email templates + send
- `app/agents/evaluation_agent.py` — retry + evaluation
- `app/orchestrator/orchestrator.py` — pipeline routing
- `app/core/logger.py` — event logging

### Flow 3: Schedule Meeting (Chat Path)

```mermaid
sequenceDiagram
    participant User
    participant Browser as Chat UI (SPA)
    participant FastAPI
    participant JWT as JWT Auth
    participant ChatA as Chat Agent
    participant OpenAI
    participant Calendar as Google Calendar
    participant SQLite

    User->>Browser: Types: "Đặt lịch họp 14h thứ Hai"
    Browser->>FastAPI: POST /chat {message, history}
    FastAPI->>JWT: get_current_user() → check cookie
    JWT->>FastAPI: user_dict
    FastAPI->>ChatA: chat(messages)
    ChatA->>OpenAI: GPT-4o completion (SYSTEM_PROMPT + history)
    OpenAI-->>ChatA: Reply + <action type="schedule">{"time":"..."}</action>
    ChatA-->>FastAPI: {reply, action}

    alt action.type == "schedule"
        FastAPI->>Calendar: Create event
        Calendar-->>FastAPI: {event_id, link}
        FastAPI->>SQLite: insert_pending_invite(token, email, event_id, data)
    else action.type == "query_calendar"
        FastAPI->>Calendar: _fetch_upcoming_events()
        Calendar-->>FastAPI: [events]
        FastAPI->>OpenAI: Re-summarize events
        OpenAI-->>FastAPI: Formatted event list
    end

    FastAPI-->>Browser: {reply, action}
    Browser->>Browser: Render reply text
    Browser->>Browser: Render action card (meeting details)
```

**Source files:**
- `app/api/v1/chat.py` — chat endpoint (line 339)
- `app/core/jwt_auth.py` — get_current_user
- `app/agents/chat_agent.py` — chat() function (line 151)
- `app/agents/chat_agent.py` — _fetch_upcoming_events() (line 68)

### Flow 4: Confirmation Link

```mermaid
sequenceDiagram
    participant Invitee
    participant Browser
    participant FastAPI
    participant SQLite
    participant Calendar as Google Calendar

    Note over Invitee: Clicks link from email: /chat/confirm/{token}
    Invitee->>Browser: Opens link
    Browser->>FastAPI: GET /chat/confirm/{token}

    FastAPI->>FastAPI: decode_token(token) → {email, action_type, data}
    alt token valid
        FastAPI->>SQLite: get_pending_invite(token)
        SQLite-->>FastAPI: {email, event_id, event_data}

        FastAPI->>Calendar: events.patch(event_id, attendees+=email)
        Calendar-->>FastAPI: Updated event

        FastAPI->>SQLite: delete_pending_invite(token)
        FastAPI-->>Browser: 200 HTML page: "Cuộc họp đã được xác nhận!"
    else token invalid
        FastAPI-->>Browser: 400 HTML page: "Liên kết không hợp lệ"
    end
```

**Source files:**
- `app/api/v1/chat.py` — confirm (line 458), decline (line 497), reschedule_confirm (line 520), reschedule_decline (line 594), cancel_confirm (line 639)
- `app/core/jwt_auth.py` — decode_token()
- `app/db/sqlite.py` — pending_invites CRUD

### Flow 5: Cancel Meeting Flow

```mermaid
sequenceDiagram
    participant Sender as Email User
    participant Poller
    participant EmailA as Email Agent
    participant CalA as Calendar Agent
    participant Calendar
    participant NotifA as Notification Agent

    Sender->>Gmail: "Hủy lịch họp 14h thứ Hai"
    Poller->>Gmail: Poll → new unread email
    Poller->>Poller: Parse + spam check → not spam
    Poller->>Poller: mark_as_read()

    Poller->>EmailA: process_email(email)
    EmailA->>OpenAI: GPT-4o classification
    OpenAI-->>EmailA: {intent: "cancel", time: "2026-04-28T14:00:00"}

    Poller->>CalA: process_cancel(email_result)
    CalA->>Calendar: events.list(±1h around time)
    Calendar-->>CalA: [matching_event]

    alt event found
        CalA->>Calendar: events.delete(event_id)
        Calendar-->>CalA: 204
        CalA-->>Poller: {status: "cancelled", event_id, attendees}
        Poller->>NotifA: send_notification(...)
        NotifA->>NotifA: _build_cancel_email()
    else event not found
        CalA-->>Poller: {status: "not_found"}
        Poller->>NotifA: send_notification(...)
        NotifA->>NotifA: _build_cancel_not_found_email()
    end

    NotifA->>Gmail: Send reply
```

**Source files:**
- `app/agents/calendar_agent.py:123-194` — process_cancel()
- `app/agents/notification_agent.py` — email template builders

### Flow 6: Dashboard Statistics

```mermaid
sequenceDiagram
    participant Browser as Chat UI
    participant FastAPI
    participant JWT as JWT Auth
    participant SQLite

    Browser->>FastAPI: GET /dashboard/stats
    FastAPI->>JWT: get_current_user() → validate JWT
    JWT-->>FastAPI: user_dict
    FastAPI->>SQLite: get_stats()
    SQLite-->>FastAPI: {total_emails, meetings_scheduled, conflicts, ...}
    FastAPI-->>Browser: JSON stats object

    Browser->>FastAPI: GET /dashboard/logs?limit=50&offset=0
    FastAPI->>JWT: get_current_user()
    FastAPI->>SQLite: get_logs(limit=50, offset=0)
    SQLite-->>FastAPI: [{id, agent, status, payload, timestamp}, ...]
    FastAPI-->>Browser: JSON array

    Browser->>Browser: Render stats cards
    Browser->>Browser: Render log table with pagination
```

**Source files:**
- `app/api/v1/chat.py:693` — dashboard_stats endpoint
- `app/api/v1/chat.py:715` — dashboard_logs endpoint
- `app/db/sqlite.py:95-118` — get_stats()
- `app/db/sqlite.py:55-64` — get_logs()

### Flow 7: Reschedule Flow

```mermaid
sequenceDiagram
    participant Sender as Email User
    participant CalA as Calendar Agent
    participant Calendar
    participant ConflictA as Conflict Agent
    participant NotifA as Notification Agent

    Sender->>Gmail: "Dời lịch 14h thứ Hai sang 10h thứ Tư"
    Note over Poller,EmailA: ... Poll + parse + classify ...
    Note over OpenAI: intent="reschedule", time="...T10:00:00", old_time="...T14:00:00"

    Poller->>CalA: process_reschedule(email_result)
    CalA->>Calendar: events.list(±1h around old_time)
    Calendar-->>CalA: [old_event]

    CalA->>Calendar: freebusy.query(new_time slot)
    Calendar-->>CalA: {busy: []}

    alt new slot free
        CalA->>Calendar: events.update(event_id, new start/end)
        Calendar-->>CalA: {updated event}
        CalA-->>Poller: {status: "rescheduled", old_start, new_start}
        NotifA->>NotifA: _build_reschedule_email()
    else new slot busy
        CalA-->>Poller: {status: "conflict", busy_slots}
        Poller->>ConflictA: find_alternatives(new_time)
        ConflictA->>Calendar: freebusy loop
        Calendar-->>ConflictA: results
        ConflictA-->>Poller: {status: "found"/"not_found", suggestions}
        NotifA->>NotifA: _build_conflict_email()
    end

    NotifA->>Gmail: Send reply
```

**Source files:**
- `app/agents/calendar_agent.py:197-301` — process_reschedule()
- `app/agents/conflict_agent.py:93-154` — find_alternatives()

---

## 4. Request Lifecycle (API-Focused)

### Email Processing Pipeline

```
INCOMING EMAIL
    │
    ▼
┌─────────────────────┐
│ 1. Gmail Poller     │  poll_gmail() — gmail_poller.py:59
│    Poll for unread   │  Infinite async loop
│    Parse raw message │  _parse_message() — gmail_poller.py:18
└────────┬────────────┘
         │ EmailSchema
         ▼
┌─────────────────────┐
│ 2. Spam Filter      │  is_spam() — spam_filter.py:68
│    Keyword matching  │  44 keyword patterns
│    Sender/Subject/Body│  Returns (bool, reason)
└────────┬────────────┘
         │ Not spam
         ▼
┌─────────────────────┐
│ 3. Mark as Read     │  _mark_as_read() — gmail_poller.py:47
│    Gmail API modify  │  removeLabelIds=["UNREAD"]
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 4. Retry Wrapper    │  evaluate_and_retry() — evaluation_agent.py:32
│    Up to 3 attempts  │  Wraps run_pipeline()
│    2s delay between  │
└────────┬────────────┘
         │ Calls run_pipeline()
         ▼
┌─────────────────────┐
│ 5. Email Classifier │  process_email() — email_agent.py:73
│    GPT-4o            │  {intent, summary, time, attendees...}
└────────┬────────────┘
         │ email_result
         ▼
┌─────────────────────┐
│ 6. Orchestrator     │  run_pipeline() — orchestrator.py
│    Route by intent   │
│    schedule → calendar_agent
│    cancel   → calendar_agent
│    reschedule → calendar_agent
│    inquiry  → calendar query
│    other    → skip
│    conflict → conflict_agent
│    always   → notification_agent
└────────┬────────────┘
         │ pipeline_result
         ▼
┌─────────────────────┐
│ 7. LLM Evaluator    │  evaluate_email() — chat_agent.py:123
│    GPT-4o checks     │  {acceptable: bool, reason: str}
│    quality of output  │
└────────┬────────────┘
         │ Evaluated result
         ▼
┌─────────────────────┐
│ 8. Database Log     │  log_event() — logger.py
│    system_logs table  │  {agent, status, payload, timestamp}
└─────────────────────┘
```

---

## 5. Authentication Flow Detail

```mermaid
stateDiagram-v2
    [*] --> Unauthenticated: No access_token cookie
    Unauthenticated --> RedirectingToGoogle: Click "Login with Google"
    RedirectingToGoogle --> GoogleConsent: GET /auth/login
    
    state GoogleConsent {
        [*] --> SelectAccount: User picks Google account
        SelectAccount --> AuthorizeScopes: Grant permissions
        AuthorizeScopes --> RedirectBack: Google redirects
    }
    
    GoogleConsent --> ProcessingCallback: GET /auth/callback?code=...&state=...
    
    state ProcessingCallback {
        [*] --> ValidateState: Check state cookie
        ValidateState --> ExchangeCode: fetch_token()
        ExchangeCode --> FetchUserInfo: GET userinfo
        FetchUserInfo --> UpsertUser: SQLite create_user()
        UpsertUser --> GenerateJWT: create_token()
        GenerateJWT --> SetCookie: HttpOnly "access_token"
    }
    
    ProcessingCallback --> Authenticated: Redirect to /ui
    
    state Authenticated {
        [*] --> LoadUI: GET /ui → chat_ui.html
        LoadUI --> VerifySession: GET /auth/me
        VerifySession --> ChatScreen: Show chat + dashboard
        ChatScreen --> SendMessage: POST /chat
        SendMessage --> ChatScreen
        ChatScreen --> ViewDashboard: GET /dashboard/*
        ViewDashboard --> ChatScreen
        ChatScreen --> Logout: POST /auth/logout
        Logout --> [*]: Clear cookie
    }
    
    Authenticated --> Unauthenticated: Logout
    Authenticated --> Unauthenticated: JWT expires (24h)
```

---

## 6. Component Interaction Matrix

| Component | Config | Auth | JWT | SQLite | Spam | Email | Cal | Conflict | Chat | Notif | Eval | Orchestrator | Poller | Gmail API | Cal API | OpenAI |
|-----------|--------|------|-----|--------|------|-------|-----|---------|------|-------|------|-------------|--------|-----------|---------|--------|
| **main.py** | ✓ | — | — | ✓ (init) | — | — | — | — | — | — | — | — | ✓ (start) | — | — | — |
| **auth_router** | ✓ | ✓ | ✓ | ✓ | — | — | — | — | — | — | — | — | — | — | — | — |
| **chat_router** | — | — | ✓ | ✓ | — | — | — | — | ✓ | — | — | — | — | — | — | — |
| **webhook_router**| — | — | — | — | — | — | — | — | — | — | — | ✓ | — | — | — | — |
| **gmail_poller** | ✓ | ✓ | — | ✓ | ✓ | — | — | — | — | — | ✓ | ✓ | — | ✓ | — | — |
| **email_agent** | ✓ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | ✓ |
| **calendar_agent**| ✓ | ✓ | — | — | — | — | — | — | — | — | — | — | — | — | ✓ | — |
| **conflict_agent**| — | ✓ (own) | — | — | — | — | — | — | — | — | — | — | — | — | ✓ | — |
| **chat_agent** | ✓ | ✓ | — | — | — | — | — | — | — | — | — | — | — | — | ✓ | ✓ |
| **notification_agent**| — | ✓ | — | — | — | — | — | — | — | — | — | — | — | ✓ | — | — |
| **evaluation_agent**| — | — | — | ✓ | — | — | — | — | ✓ | — | — | — | — | — | — | — |
| **orchestrator** | — | — | — | — | — | ✓ | ✓ | ✓ | — | ✓ | — | — | — | — | — | — |

✓ = Direct dependency/call | — = No interaction

---

## 7. Key File Reference

| File | Lines | Primary Role | Key Exports |
|------|-------|-------------|-------------|
| `app/main.py` | 68 | Entry point | FastAPI app, startup, routes |
| `app/core/config.py` | ~50 | Configuration | `Settings` (Pydantic BaseSettings) |
| `app/core/auth.py` | 84 | Google OAuth | `get_gmail_service()`, `get_calendar_service()` |
| `app/core/jwt_auth.py` | 78 | JWT operations | `create_token()`, `decode_token()`, `get_current_user()` |
| `app/core/logger.py` | ~30 | Event logging | `log_event()` |
| `app/core/gmail_poller.py` | 143 | Email ingestion | `poll_gmail()` |
| `app/api/v1/auth.py` | ~252 | User auth | `google_auth_url()`, `callback()`, `me()`, `logout()` |
| `app/api/v1/chat.py` | ~750 | Chat + dashboard | `chat()`, 6 confirmation endpoints, `dashboard_stats()`, `dashboard_logs()` |
| `app/api/v1/webhook.py` | ~30 | Webhook | `gmail_webhook()` |
| `app/agents/spam_filter.py` | ~120 | Spam detection | `is_spam()` |
| `app/agents/email_agent.py` | ~140 | Intent classification | `process_email()` |
| `app/agents/calendar_agent.py` | 301 | Calendar operations | `process_schedule()`, `process_cancel()`, `process_reschedule()` |
| `app/agents/conflict_agent.py` | ~175 | Alternative slots | `find_alternatives()` |
| `app/agents/chat_agent.py` | ~200 | Chat interaction | `chat()`, `evaluate_email()` |
| `app/agents/notification_agent.py` | ~380 | Email reply | `send_notification()`, `send_reply()` |
| `app/agents/evaluation_agent.py` | ~120 | Quality evaluation | `evaluate_and_retry()` |
| `app/orchestrator/orchestrator.py` | ~150 | Pipeline routing | `run_pipeline()` |
| `app/db/sqlite.py` | 118 | Database layer | `init_db()`, `get_logs()`, `get_stats()`, user + pending CRUD |
| `app/schemas/email.py` | ~20 | Data models | `EmailSchema` |
| `app/chat_ui.html` | 1623 | Frontend SPA | Chat UI + Dashboard (vanilla JS) |

---

*End of Architecture Overview*
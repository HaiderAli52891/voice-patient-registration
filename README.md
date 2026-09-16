# Voice AI Patient Registration

A phone number you can call. An AI intake coordinator answers, collects U.S.
patient demographics in a natural conversation, reads them back for
confirmation, and writes them to a database that a REST API and a staff
dashboard read from.

---

## Submission details

Fill these in before sending, then delete this note.

| Item | Value |
| --- | --- |
| Repository | `https://github.com/<you>/voice-patient-registration` |
| Phone number | `+1 (XXX) XXX-XXXX` |
| API base URL | `https://<your-app>.up.railway.app` |
| Dashboard | `https://<your-app>.up.railway.app/dashboard` |
| API docs | `https://<your-app>.up.railway.app/docs` |
| Credentials | Dashboard is public / Basic auth `reviewer` / `<password>` |

**Fastest way to verify:** call the number, register yourself, then open
`/dashboard` — your record appears with the full call transcript beneath it.
Call again from the same phone and the agent greets you by name and offers to
update instead of duplicating.

---

## Architecture

```
  ┌──────────┐   PSTN    ┌─────────────┐   webhooks   ┌──────────────────────┐
  │  Caller  │──────────▶│   Twilio    │─────────────▶│  app/voice/          │
  └──────────┘           │  STT + TTS  │◀─── TwiML ───│  twilio_routes.py    │
                         └─────────────┘              └──────────┬───────────┘
                                                                 │ transcript
                                                                 ▼
                                                     ┌──────────────────────┐
                                        tool calls   │  app/voice/agent.py  │
                                      ┌──────────────│  OpenAI + tools      │
                                      │              └──────────┬───────────┘
                                      ▼                         │
                         ┌──────────────────────┐               │
                         │  app/validators.py   │               │
                         │  field-level rules   │               │
                         └──────────────────────┘               ▼
                                                     ┌──────────────────────┐
  ┌──────────┐   HTTP    ┌──────────────────────┐    │    app/crud.py       │
  │  Client  │──────────▶│  app/api/patients.py │───▶│    service layer     │
  └──────────┘           │  REST + validation   │    └──────────┬───────────┘
                         └──────────────────────┘               │
  ┌──────────┐           ┌──────────────────────┐               ▼
  │  Staff   │──────────▶│ app/api/dashboard.py │    ┌──────────────────────┐
  └──────────┘           └──────────────────────┘    │  SQLite / Postgres   │
                                                     │  patients, call_logs │
                                                     └──────────────────────┘
```

Four layers, each replaceable without touching the others:

| Layer | Module | Responsibility |
| --- | --- | --- |
| Telephony | `app/voice/twilio_routes.py`, `app/voice/vapi_routes.py` | Speech in, speech out. Knows nothing about patients. |
| Conversation | `app/voice/agent.py`, `app/voice/prompts.py` | Runs the LLM loop and executes tools. Knows nothing about Twilio. |
| Domain | `app/validators.py`, `app/crud.py` | Validation rules and every database read/write. |
| Interfaces | `app/api/patients.py`, `app/api/dashboard.py` | REST API and staff dashboard over the same service layer. |

The proof that the seam is real: `scripts/simulate_call.py` drives the exact
same agent from your terminal with no telephony at all, and the Vapi webhook
reuses the same validators and service functions as the Twilio path.

### The one design decision that matters most

**The LLM asks the questions; Python decides what is valid.**

Language models are good at conversation and bad at arithmetic constraints.
Ask GPT to check whether `9747` is a valid ZIP and it will often say yes. So
the agent has no authority to accept a value: every captured field goes
through the `save_field` tool, which calls `app/validators.py` and returns
either the normalised value or an error string written for the model to
paraphrase out loud:

```json
{"ok": false, "field": "zip_code",
 "message": "A ZIP code is five digits. Please repeat it one digit at a time."}
```

The same validators run again server-side inside `POST /patients`, so a
misbehaving agent still cannot write a bad record. That satisfies the brief's
"do not rely solely on the voice agent for validation" requirement structurally
rather than by duplicating logic.

Two more consequences fall out of this:

- **`save_patient` refuses to write unless `confirmed=true`.** The read-back
  step cannot be skipped by a model in a hurry.
- **State lives in `call_logs.collected_json`, not in the prompt.** It is
  re-injected as a system line on every turn, so a trimmed context window, a
  restarted worker, or a resumed call all behave identically.

Full prompt-engineering rationale is commented at the top of
`app/voice/prompts.py`.

---

## Tech stack, and why

| Choice | Reason | What I gave up |
| --- | --- | --- |
| **FastAPI** | Pydantic validation, automatic OpenAPI docs at `/docs`, async webhooks. One framework for telephony webhooks, REST and dashboard. | Nothing meaningful at this size. |
| **Twilio `<Gather input="speech">`** | Working STT + neural TTS in ~40 lines. No media streams, no WebSocket plumbing, no separate Deepgram/ElevenLabs accounts. | ~1s turn latency and no barge-in. The Vapi path (Option B) removes both. |
| **OpenAI `gpt-4o-mini` + tool calling** | Fast enough for phone turns, reliable structured tool calls, cheap. | GPT-4o is better at messy corrections; swap `OPENAI_MODEL` if you want it. |
| **SQLAlchemy 2.0** | Same models work on SQLite locally and Postgres in production — one env var. | — |
| **SQLite default, Postgres supported** | Zero setup for a reviewer cloning the repo. Persistence across restarts is what the brief asks for, and SQLite gives that. | Single writer. Set `DATABASE_URL` to Postgres for concurrency. |
| **Server-rendered Jinja dashboard** | One read-only page. A React build step would cost minutes and buy nothing. | No live updates; refresh the page. |

---

## Data model

`patients` — every field from the brief, plus `deleted_at` (soft delete) and
`source` (`voice` / `api` / `seed`).

Storage normalisation, so lookups are exact and comparisons work in SQL:

- `date_of_birth` stored ISO `YYYY-MM-DD`; presented as `MM/DD/YYYY`.
- `phone_number` stored as 10 bare digits; presented as `(555) 123-4567`.
- `state` stored as the 2-letter abbreviation; callers may say "California".
- `sex` constrained to the four enum values by a `CHECK`.

`call_logs` — one row per call with the running transcript, the JSON of
fields collected so far, an outcome (`completed`, `abandoned_*`, `db_error`,
`llm_error`), and a foreign key to the patient when one was written. Written
incrementally, so a dropped call still leaves everything the caller said.

---

## REST API

Base URL: your deployment. Interactive docs at `/docs`.

Every response uses the same envelope:

```json
{ "data": { }, "error": null }
```

| Method | Endpoint | Notes |
| --- | --- | --- |
| `GET` | `/patients` | Filters: `?last_name=`, `?date_of_birth=MM/DD/YYYY`, `?phone_number=`, `?include_deleted=`, `?limit=`, `?offset=` |
| `GET` | `/patients/{id}` | 404 if missing or soft-deleted |
| `POST` | `/patients` | 201 on success, 422 with per-field messages |
| `PUT` | `/patients/{id}` | Partial updates allowed (`PATCH` is aliased) |
| `DELETE` | `/patients/{id}` | Soft delete — sets `deleted_at`, keeps the row |
| `GET` | `/calls` | Recent call transcripts |
| `GET` | `/health` | Liveness plus database check |
| `GET` | `/dashboard` | Staff web UI |

Status codes: `200`, `201`, `400`, `401`, `404`, `422`, `500`.

Validation errors name the field:

```jsonc
// POST /patients with "zip_code": "9747"  ->  422
{
  "data": null,
  "error": {
    "code": "validation_error",
    "fields": { "zip_code": "A ZIP code is five digits. Please repeat it one digit at a time." }
  }
}
```

### Quick check

```bash
BASE=https://your-app.up.railway.app

curl -s $BASE/health

curl -s -X POST $BASE/patients -H 'Content-Type: application/json' -d '{
  "first_name":"Jane","last_name":"Doe","date_of_birth":"03/05/1985",
  "sex":"female","phone_number":"(555) 123-4567",
  "address_line_1":"742 Evergreen Terrace","city":"springfield",
  "state":"Oregon","zip_code":"97477"}'

curl -s "$BASE/patients?last_name=Doe"
```

---

## Running it locally

### 1. Install

```bash
git clone <your-repo-url>
cd voice-patient-registration

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Open `.env` and set at minimum `OPENAI_API_KEY`. Everything else has a working
default.

### 3. Create the database and seed it

```bash
python -m app.seed
```

Creates `data/patients.db` with two demo patients (Jane Doe, Miguel Alvarez).

### 4. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

- Dashboard: <http://localhost:8000/dashboard>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

### 5. Talk to the agent without a phone

```bash
python -m scripts.simulate_call
```

A terminal conversation using the identical prompt, tools, validators and
database writes as a real call. Pass a phone number to test returning-caller
detection:

```bash
python -m scripts.simulate_call +15551234567    # matches seeded Jane Doe
```

### 6. Run the tests

```bash
pytest
```

46 tests: validator unit tests, REST integration tests, and telephony webhook tests against a throwaway
SQLite file. No network calls, no API key needed.

---

## Connecting a real phone number

### Option A — Twilio (what is deployed)

1. **Expose your local server** (skip if already deployed):

   ```bash
   ngrok http 8000
   ```

   Copy the `https://` URL into `PUBLIC_BASE_URL` in `.env`.

2. **Buy a number**: Twilio Console → Phone Numbers → Buy a number → check
   *Voice* → pick a US local number.

3. **Point it at the app**: open the number's configuration and set

   | Setting | Value |
   | --- | --- |
   | A call comes in | Webhook · `https://<your-host>/voice/incoming` · HTTP POST |
   | Primary handler fails | Webhook · `https://<your-host>/voice/fallback` · HTTP POST |
   | Call status changes | `https://<your-host>/voice/status` · HTTP POST |

4. **Fill in `.env`**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_PHONE_NUMBER`. Once you are on HTTPS, set
   `VALIDATE_TWILIO_SIGNATURE=true` to reject forged webhooks.

5. **Call it.** Trial accounts play a short Twilio message first and can only
   call verified numbers; upgrading removes both.

### Option B — Vapi or Retell (lower latency, real barge-in)

The webhook is already built at `POST /vapi/tool`. Create a Vapi assistant,
paste `SYSTEM_PROMPT` from `app/voice/prompts.py` into its system message, and
add three custom tools pointing at `https://<your-host>/vapi/tool`:

| Tool | Parameters |
| --- | --- |
| `save_field` | `field` (string), `value` (string) |
| `lookup_patient` | `phone_number` (string) |
| `save_patient` | `confirmed` (boolean) |

Set the server URL secret to match `VAPI_SECRET`, and point the assistant's
server-message URL at `POST /vapi/events` to store end-of-call transcripts.
Attach a phone number in the Vapi dashboard. No application code changes —
the validators and service layer are shared with the Twilio path.

---

## Deploying

### Railway (what I used)

1. Push to GitHub, then Railway → New Project → Deploy from GitHub repo.
2. Railway detects the `Dockerfile`. Add the variables from `.env.example`.
3. Set `PUBLIC_BASE_URL` to the generated domain.
4. **Persistence:** either add a Postgres plugin and set
   `DATABASE_URL=${{Postgres.DATABASE_URL}}` (recommended), or attach a volume
   mounted at `/app/data` so the SQLite file survives redeploys. Without one of
   these, a redeploy wipes the database.
5. Point the Twilio webhooks at the Railway domain.

### Render / Fly.io

Same shape. Build with the `Dockerfile`, start with
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`,
and attach a disk at `/app/data` if staying on SQLite.

---

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | yes | — | LLM access |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Model used for the agent |
| `DATABASE_URL` | no | `sqlite:///./data/patients.db` | SQLite or Postgres |
| `PUBLIC_BASE_URL` | for telephony | `http://localhost:8000` | Public HTTPS URL |
| `TWILIO_ACCOUNT_SID` | for telephony | — | Twilio credentials |
| `TWILIO_AUTH_TOKEN` | for telephony | — | Also used for signature checks |
| `TWILIO_PHONE_NUMBER` | for telephony | — | Shown on the dashboard |
| `TWILIO_VOICE` | no | `Polly.Joanna-Neural` | TTS voice |
| `VALIDATE_TWILIO_SIGNATURE` | no | `false` | Reject forged webhooks |
| `VAPI_SECRET` | Option B only | — | Shared webhook secret |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | no | blank | Basic auth on `/dashboard` |
| `LOG_LEVEL` | no | `INFO` | Logging verbosity |
| `ENVIRONMENT` | no | `development` | Label only |

No secret is read from anywhere but the environment. `.env` is gitignored.

---

## Edge cases and how each is handled

| Situation | Behaviour |
| --- | --- |
| Invalid date of birth ("February 40th", a future date) | `save_field` rejects it with a specific message; the agent re-asks for that field only. |
| 3-digit phone number | Rejected with "needs to be 10 digits including the area code — I heard 3." |
| Caller corrects a field ("Davis with an S") | The agent calls `save_field` again; the new value overwrites and is confirmed aloud. |
| Caller wants to start over | `reset_registration` clears collected state; the agent restarts from the name. |
| Caller says nothing | `actionOnEmptyResult` fires `/voice/turn` with empty speech; the agent re-prompts instead of hanging up. |
| Database write fails | `save_patient` returns `ok:false`, the outcome is logged as `db_error`, and the caller is told their information was not saved and asked to call back. They never get silence. |
| LLM call fails or times out | Caught in `run_turn`; the caller hears the fallback apology and the call ends cleanly. Outcome logged as `llm_error`. |
| Application crashes entirely | Twilio's fallback URL hits `/voice/fallback`, which serves static TwiML rather than dead air. |
| Call drops mid-registration | `/voice/status` records `abandoned_*`; the partial transcript and collected fields stay in `call_logs` for follow-up. |
| Returning caller | Caller ID is matched against existing patients. The agent greets them by name and offers an update; `save_patient` updates rather than duplicating. |
| Unhandled server exception | Global handler returns the standard envelope with a 500 and logs the traceback; no stack trace leaks to the client. |

---

## Observability

Everything goes to stdout (captured by Railway/Render) and `logs/app.log`:

```
call.started sid=CA123... from=+15125550147
caller.said sid=CA123... conf=0.94 text='my name is Jane Doe'
field.saved first_name='Jane'
field.rejected zip_code='9747' -> A ZIP code is five digits...
registration.created patient_id=8f3c... payload={"first_name": "Jane", ...}
call.completed sid=CA123... patient_id=8f3c...
```

Full transcripts are also stored per call and rendered at the bottom of the
dashboard. Credentials are never logged — `DATABASE_URL` is truncated at the
`@` before it reaches a log line.

---

## Bonus items included

- **Duplicate detection** — caller ID matched against existing records; the
  agent offers to update instead of creating a second row.
- **Call transcripts** — stored in `call_logs`, linked to the patient, exposed
  at `GET /calls` and on the dashboard.
- **Dashboard** — searchable patient ledger at `/dashboard` with expandable
  records and recent call transcripts.
- **Automated tests** — 46 validator, API and telephony-webhook tests.
- **Multi-language** — partially there. `preferred_language` is collected and
  stored, and the model will answer in Spanish if addressed in Spanish, but
  Twilio's `<Gather>` is pinned to `en-US`, so Spanish speech recognition is
  unreliable. Properly done, this needs the Vapi path with a multilingual
  transcriber. Not claimed as complete.

---

## Known limitations and trade-offs

1. **Turn-based, not full duplex.** `<Gather>` waits for the caller to stop
   speaking. Roughly a second of latency per turn and no interrupting the
   agent mid-sentence. Vapi or a Twilio Media Streams pipeline fixes this; it
   was not worth the build time here.
2. **Conversation history is in process memory.** `_HISTORIES` in
   `twilio_routes.py` assumes one web worker. Collected fields are persisted
   after every tool call, so a restart loses phrasing context but not patient
   data. Redis would make this horizontal.
3. **SQLite by default.** Fine for a demo and for persistence across restarts;
   single-writer under real concurrency. `DATABASE_URL` switches to Postgres
   with no code change.
4. **`create_all()` instead of migrations.** Alembic is the right answer for
   anything long-lived.
5. **No HIPAA posture.** No encryption at rest, no BAA, no audit trail beyond
   application logs, transcripts stored in plaintext. Per the brief, do not put
   real patient data in this.
6. **The dashboard is open by default.** Set `DASHBOARD_USER` and
   `DASHBOARD_PASSWORD` to gate it. The REST API has no auth at all — it is a
   reviewable demo, not an exposed service.
7. **Speech recognition of spelled names is imperfect.** "D-A-V-I-S" often
   arrives as "D A V I S" and is handled, but heavily accented spelling still
   fails sometimes. A confidence threshold plus explicit re-confirmation would
   help.
8. **Address is not verified.** A real intake would run it through USPS or
   Google Address Validation; here only format is checked.

---

## Next steps, given more time

1. Swap `<Gather>` for Vapi to get barge-in and sub-500ms turns.
2. Move conversation history to Redis and run multiple workers.
3. Alembic migrations and a Postgres-backed staging environment.
4. Appointment scheduling after registration, against a mock slot table.
5. API authentication (API keys or JWT) and per-IP rate limiting.
6. Confidence-score gating: when Twilio's STT confidence is below ~0.6,
   explicitly re-confirm rather than trusting the transcript.
7. Address validation against USPS.
8. A conversation-level eval harness — scripted callers with accents,
   corrections and interruptions, scored automatically, so prompt changes can
   be measured instead of guessed at.

---

## Project layout

```
voice-patient-registration/
├── app/
│   ├── main.py              FastAPI app, error envelope, /health
│   ├── config.py            Environment-driven settings
│   ├── database.py          Engine, session, init_db
│   ├── models.py            Patient and CallLog ORM models
│   ├── schemas.py           Request/response schemas
│   ├── crud.py              Service layer — all DB access
│   ├── validators.py        Field rules shared by API and agent
│   ├── logging_config.py    stdout + file logging
│   ├── seed.py              Demo records
│   ├── api/
│   │   ├── patients.py      REST endpoints
│   │   └── dashboard.py     Staff web UI route
│   ├── voice/
│   │   ├── prompts.py       System prompt + tool schema (documented)
│   │   ├── agent.py         LLM loop and tool execution
│   │   ├── twilio_routes.py Twilio webhooks / TwiML
│   │   └── vapi_routes.py   Vapi / Retell webhooks
│   └── templates/
│       └── dashboard.html
├── scripts/
│   └── simulate_call.py     Terminal conversation with the agent
├── tests/
│   ├── test_validators.py
│   └── test_api.py
├── Dockerfile
├── Procfile
├── requirements.txt
└── .env.example
```

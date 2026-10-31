# Gemini API — ClinicFlow Usage Spec

## What we use it for
Gemini is used OFFLINE ONLY to generate FR/EN notification message templates.
Templates are stored in the `notification_templates` table with placeholders
({patient_name}, {time}, {doctor}). At send time, placeholders are filled
LOCALLY — no patient data ever reaches the Gemini API.

This is a health app. Free-tier prompts may be used to improve Google's
products. Therefore: zero PII, zero patient data, zero clinic data in any
prompt. Ever.

## Model
Use `gemini-3-1-pro` (paid tier):
- Available via Google AI Studio: aistudio.google.com

## Rate limits to respect
- We make ~10–20 API calls total (offline, one-time template generation)
- Pro is paid per token — keep prompts tight
- Pro: $4.00 per 1M input tokens, $18.00 per 1M output tokens
- Template generation costs fractions of a cent total — not a concern

## Setup
Get your key at aistudio.google.com → API Keys → Create key.
Add to backend/.env:
```
GEMINI_API_KEY=your-key-here
```
Add to backend/.env.example:
```
GEMINI_API_KEY=
```

## How to call it (Python)
Gemini uses its own SDK, not the OpenAI-compatible endpoint:
```python
pip install google-genai
```

```python
from google import genai

client = genai.Client(api_key=settings.GEMINI_API_KEY)

response = client.models.generate_content(
    model="gemini-3-1-pro",
    contents="Your prompt here"
)
text = response.text
```

## The offline template generation script
Location: `backend/scripts/generate_templates.py`
Run once manually: `python scripts/generate_templates.py`
What it does:
1. Calls Gemini with a prompt asking for FR/EN message templates
2. Parses the response as JSON
3. Inserts rows into `notification_templates`

Prompt must instruct Gemini to return JSON only, no markdown, no preamble.
Parse safely with try/except — strip ```json fences before parsing.

## Templates to generate (Phase 5)
| key | description |
|---|---|
| delay_notice | Doctor running late — please arrive later |
| arrival_reminder | Your appointment is soon — please head to clinic |
| you_are_next | You are next in queue |
| session_started | Doctor is ready for you now |
| completed | Your appointment is complete |

Each template in both `fr` and `en`, for both `whatsapp` and `sms` channels.
That's ~20 rows total — well within free tier limits.

## What Gemini is NOT used for
- Real-time inference during patient sessions
- Queue predictions (that's our own heuristic/ML, on our own data)
- Anything that touches real appointment, patient, or clinic data

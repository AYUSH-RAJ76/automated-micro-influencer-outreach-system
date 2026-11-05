"""Groq powered, validated personalization with actionable provider errors."""
import json
import random
import re
import time

import httpx

from app.core.config import get_settings


def words(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value or ""))


class PersonalizationError(RuntimeError):
    pass


def _provider_message(response: httpx.Response) -> str:
    try:
        error=response.json().get("error", {})
        message=str(error.get("message", ""))
        failed_generation=error.get("failed_generation")
        if failed_generation:
            message += f" Model output rejected: {str(failed_generation)[:250]}"
        return message[:500]
    except (ValueError, AttributeError):
        return ""


def _post_with_retries(client, payload, settings):
    max_retries = max(0, settings.groq_max_retries)
    for attempt in range(max_retries + 1):
        try:
            response = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            if attempt >= max_retries:
                raise PersonalizationError(
                    f"Groq request timed out after {attempt + 1} attempt(s). Increase GROQ_REQUEST_TIMEOUT_SECONDS or retry."
                ) from exc
            time.sleep(settings.groq_retry_backoff_seconds * (2**attempt) * random.uniform(0.75, 1.25))
            continue
        except httpx.NetworkError as exc:
            if attempt >= max_retries:
                raise PersonalizationError("Could not connect to Groq after bounded retries. Check network access and try again.") from exc
            time.sleep(settings.groq_retry_backoff_seconds * (2**attempt) * random.uniform(0.75, 1.25))
            continue

        if response.is_success:
            return response

        detail = _provider_message(response)
        if response.status_code == 401:
            raise PersonalizationError("Groq rejected the API credentials (401). Check GROQ_API_KEY in .env, then restart Streamlit.")
        if response.status_code == 403:
            raise PersonalizationError(f"Groq denied this request (403). Check account access or model availability. {detail}")
        if (response.status_code == 429 or response.status_code >= 500) and attempt < max_retries:
            time.sleep(settings.groq_retry_backoff_seconds * (2**attempt) * random.uniform(0.75, 1.25))
            continue
        if response.status_code == 429:
            raise PersonalizationError(f"Groq rate limited this request (429). Wait briefly and retry. {detail}")
        raise PersonalizationError(f"Groq rejected the request (HTTP {response.status_code}). Check GROQ_MODEL and request settings. {detail}")

    raise PersonalizationError("Groq request failed after bounded retries.")


def generate(influencer, brand=None, client=None):
    settings = get_settings()
    if not settings.groq_api_key:
        raise PersonalizationError("GROQ_API_KEY is missing. Add it to .env and restart Streamlit to generate real AI messages.")
    brand = brand or settings.brand_name
    videos = [video.title for video in influencer.videos[:3]]
    context = {
        "name": influencer.name,
        "niche": influencer.niche,
        "recent_titles": videos,
        "themes": influencer.themes,
        "content_style": influencer.content_style,
        "brand": brand,
        "brand_is_illustrative": True,
    }
    instructions = (
        "Write personalized collaboration outreach using only the supplied creator profile. "
        "Do not claim you watched videos, used a product, or know audience demographics. "
        "The brand is illustrative. Make the email_body 60 to 90 words and dm_body 15 to 30 words. "
        "Use a concise, natural subject. All values must be plain strings."
    )
    output_schema={
        "type":"json_schema",
        "json_schema":{
            "name":"creator_outreach_draft",
            "strict":True,
            "schema":{
                "type":"object",
                "properties":{
                    "subject":{"type":"string"},
                    "email_body":{"type":"string"},
                    "dm_body":{"type":"string"},
                },
                "required":["subject","email_body","dm_body"],
                "additionalProperties":False,
            },
        },
    }
    payload = {
        "model": settings.groq_model,
        "messages": [
            {"role":"system","content":instructions},
            {"role":"user","content":"Creator profile JSON:\n"+json.dumps(context)},
        ],
        "response_format": output_schema,
        "max_completion_tokens": 1200,
    }
    timeout = httpx.Timeout(
        connect=min(10.0, settings.groq_request_timeout_seconds),
        read=settings.groq_request_timeout_seconds,
        write=min(20.0, settings.groq_request_timeout_seconds),
        pool=min(10.0, settings.groq_request_timeout_seconds),
    )
    api_client = client or httpx.Client(timeout=timeout)

    # One content correction retry after the initial response; transport retries
    # are separately bounded by GROQ_MAX_RETRIES.
    for generation_attempt in range(2):
        response = _post_with_retries(api_client, payload, settings)
        try:
            raw = response.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if generation_attempt == 0:
                payload["messages"][0]["content"] = instructions + " Follow the required JSON schema exactly."
                continue
            raise PersonalizationError("Groq responded, but the output was not valid JSON with the expected fields.") from exc
        if (
            isinstance(data,dict)
            and all(isinstance(data.get(field),str) for field in ("subject","email_body","dm_body"))
            and 60 <= words(data["email_body"]) <= 90
            and 15 <= words(data["dm_body"]) <= 30
            and data["subject"].strip()
        ):
            return data, context
        payload["messages"][0]["content"] = instructions + " The prior draft violated the word limits. Follow the required JSON schema and word counts exactly."
    raise PersonalizationError("Groq returned messages outside the required word counts after two generations.")

"""Quota-aware, key-safe client for the public YouTube Data API v3."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
BASE = "https://www.googleapis.com/youtube/v3"
QUERIES = [
    "AI tutorials",
    "Python programming",
    "Machine learning tutorials",
    "Software engineering",
    "Developer tools",
    "Generative AI tutorials",
    "Technology education",
]


def queries_for_niche(niche: str) -> list[str]:
    normalized = " ".join((niche or "").strip().lower().split())
    if normalized in {"technology", "tech", "technology education"} or not normalized:
        return QUERIES.copy()
    return list(dict.fromkeys([
        normalized,
        f"{normalized} tutorials",
        f"{normalized} education",
        f"{normalized} explained",
        f"{normalized} tools",
    ]))

# Google documents search.list at 100 quota units; channels, playlistItems,
# and videos list calls are generally 1 unit each.
QUOTA_COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}
_SHARED_SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class YouTubeAPIError(RuntimeError):
    """Sanitized API error. Never includes the request URL or API key."""

    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class YouTubeQuotaExceeded(YouTubeAPIError):
    pass


class YouTubeClient:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        settings = get_settings()
        self.key = api_key if api_key is not None else settings.youtube_api_key
        timeout = httpx.Timeout(
            connect=settings.youtube_connect_timeout,
            read=settings.youtube_read_timeout,
            write=settings.youtube_write_timeout,
            pool=settings.youtube_pool_timeout,
        )
        self.client = client or httpx.Client(timeout=timeout)
        self.cache: dict[str, dict[str, Any]] = {}
        self.stats: dict[str, Any] = {"request_count": 0, "quota_units_estimated": 0, "by_endpoint": {}, "retries": 0}
        self.max_retries = max(0, settings.youtube_max_retries)
        self.backoff_seconds = max(0.0, settings.youtube_retry_backoff_seconds)
        self.cache_ttl_seconds = max(0, settings.youtube_cache_ttl_seconds)

    @staticmethod
    def _error_details(response: httpx.Response) -> tuple[str | None, str]:
        """Return Google's structured reason and a safe, compact message."""
        reason = None
        message = ""
        try:
            payload = response.json()
            error = payload.get("error", {})
            message = str(error.get("message", ""))
            errors = error.get("errors", [])
            if errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason")
            reason = reason or error.get("status")
        except (ValueError, AttributeError):
            message = response.text[:500]
        return reason, message[:500]

    def _safe_message(self, message: str) -> str:
        return message.replace(self.key, "[REDACTED]") if self.key else message

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.key:
            raise YouTubeAPIError("YOUTUBE_API_KEY is missing. Add it to .env to run real discovery.")
        safe_params = dict(params)
        # Cache at the API operation level. API key is never part of the cache key.
        cache_key = endpoint + repr(sorted((k, str(v)) for k, v in safe_params.items()))
        bypass_search_cache = endpoint == "search" and getattr(self, "bypass_search_cache", False)
        if cache_key in self.cache and not bypass_search_cache:
            return self.cache[cache_key]
        if endpoint == "search" and self.cache_ttl_seconds and not bypass_search_cache:
            shared = _SHARED_SEARCH_CACHE.get(cache_key)
            if shared and time.monotonic() - shared[0] < self.cache_ttl_seconds:
                self.cache[cache_key] = shared[1]
                return shared[1]

        for attempt in range(self.max_retries + 1):
            try:
                self.stats["request_count"] += 1
                self.stats["quota_units_estimated"] += QUOTA_COST.get(endpoint, 1)
                endpoint_stats = self.stats["by_endpoint"].setdefault(endpoint, {"requests": 0, "quota_units_estimated": 0})
                endpoint_stats["requests"] += 1
                endpoint_stats["quota_units_estimated"] += QUOTA_COST.get(endpoint, 1)
                response = self.client.get(f"{BASE}/{endpoint}", params=safe_params, headers={"x-goog-api-key": self.key})

                if response.is_success:
                    data = response.json()
                    self.cache[cache_key] = data
                    if endpoint == "search" and self.cache_ttl_seconds:
                        _SHARED_SEARCH_CACHE[cache_key] = (time.monotonic(), data)
                    return data

                reason, body_message = self._error_details(response)
                safe_body = self._safe_message(body_message)
                quota_exhausted = reason == "quotaExceeded"
                if quota_exhausted:
                    raise YouTubeQuotaExceeded(
                        "YouTube daily API quota is exhausted (quotaExceeded). Try again after the quota reset or request more quota.",
                        status_code=response.status_code,
                        reason=reason,
                    )

                transient_api_limit = reason in {"rateLimitExceeded", "userRateLimitExceeded"}
                transient_status = response.status_code == 429 or response.status_code >= 500
                if (transient_api_limit or transient_status) and attempt < self.max_retries:
                    self._backoff(attempt, endpoint, reason or str(response.status_code))
                    continue

                message = f"YouTube API {endpoint} failed ({response.status_code}; {reason or 'unknown reason'})."
                if safe_body:
                    message += f" {safe_body}"
                raise YouTubeAPIError(message, status_code=response.status_code, reason=reason)

            except YouTubeQuotaExceeded:
                # A quota reset cannot be accelerated by retrying.
                raise
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise YouTubeAPIError(
                        f"YouTube API {endpoint} timed out after {attempt + 1} attempts. Check connectivity or adjust YOUTUBE_READ_TIMEOUT."
                    ) from exc
                self._backoff(attempt, endpoint, "timeout")
            except httpx.NetworkError as exc:
                if attempt >= self.max_retries:
                    raise YouTubeAPIError(f"Network error contacting YouTube API {endpoint} after {attempt + 1} attempts.") from exc
                self._backoff(attempt, endpoint, "network error")
        raise YouTubeAPIError(f"YouTube API {endpoint} failed after bounded retries.")

    def _backoff(self, attempt: int, endpoint: str, reason: str) -> None:
        self.stats["retries"] += 1
        delay = self.backoff_seconds * (2**attempt) * random.uniform(0.75, 1.25)
        log.warning("Retrying YouTube API endpoint=%s reason=%s attempt=%d wait_seconds=%.2f", endpoint, reason, attempt + 1, delay)
        time.sleep(delay)

    def search_channels(self, query: str, limit: int) -> list[str]:
        ids: list[str] = []
        token = None
        while len(ids) < limit:
            params: dict[str, Any] = {"part": "snippet", "type": "channel", "q": query, "maxResults": min(50, limit - len(ids))}
            if token:
                params["pageToken"] = token
            data = self._get("search", params)
            ids.extend(item["id"]["channelId"] for item in data.get("items", []) if item.get("id", {}).get("channelId"))
            token = data.get("nextPageToken")
            if not token:
                break
        # Search results may repeat a channel within a query's pages.
        return list(dict.fromkeys(ids))

    def channels(self, ids: list[str]) -> list[dict]:
        output: list[dict] = []
        unique_ids = list(dict.fromkeys(ids))
        for start in range(0, len(unique_ids), 50):
            output.extend(self._get("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(unique_ids[start:start + 50])}).get("items", []))
        return output

    def recent_videos(self, channel_id: str, limit: int = 10, uploads_playlist_id: str | None = None) -> list[dict]:
        """Fetch recent uploads via playlistItems.list (1 unit), then batch videos.list (1 unit).

        This avoids search.list per creator, which would cost 100 units for each channel.
        """
        if not uploads_playlist_id:
            return []
        playlist = self._get("playlistItems", {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(limit, 50),
        })
        video_ids = list(dict.fromkeys(
            item.get("contentDetails", {}).get("videoId")
            or item.get("snippet", {}).get("resourceId", {}).get("videoId")
            for item in playlist.get("items", [])
        ))
        video_ids = [video_id for video_id in video_ids if video_id]
        if not video_ids:
            return []
        videos: list[dict] = []
        for start in range(0, len(video_ids), 50):
            videos.extend(self._get("videos", {"part": "snippet,statistics", "id": ",".join(video_ids[start:start + 50])}).get("items", []))
        return videos

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.models import Influencer, RecentVideo, SourceEvidence
from app.services import youtube_client as yt_module
from app.services.discovery import run_discovery
from app.services.youtube_client import YouTubeAPIError, YouTubeClient, YouTubeQuotaExceeded


def make_client(handler, monkeypatch, *, retries=2):
    monkeypatch.setattr(yt_module, "get_settings", lambda: Settings(
        youtube_api_key="test-secret-key",
        youtube_max_retries=retries,
        youtube_retry_backoff_seconds=0,
        youtube_connect_timeout=1,
        youtube_read_timeout=2,
        youtube_write_timeout=1,
        youtube_pool_timeout=1,
    ))
    monkeypatch.setattr(yt_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(yt_module.random, "uniform", lambda _a, _b: 1)
    return YouTubeClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def api_error(reason, message="Request limited"):
    return httpx.Response(429, json={"error": {"code": 429, "message": message, "errors": [{"reason": reason}]}})


def test_429_quota_exceeded_is_not_retried_or_leaked(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return api_error("quotaExceeded", "Daily limit reached")

    client = make_client(handler, monkeypatch, retries=3)
    with pytest.raises(YouTubeQuotaExceeded, match="daily API quota is exhausted"):
        client.search_channels("AI tutorials", 10)
    assert len(calls) == 1
    assert "test-secret-key" not in str(calls[0].url)
    assert calls[0].headers["x-goog-api-key"] == "test-secret-key"
    assert "test-secret-key" not in str(client.stats)


def test_429_rate_limit_retries_then_succeeds(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        if len(calls) == 1:
            return api_error("rateLimitExceeded")
        return httpx.Response(200, json={"items": [{"id": {"channelId": "UC1"}}]})

    client = make_client(handler, monkeypatch)
    assert client.search_channels("Python", 10) == ["UC1"]
    assert len(calls) == 2
    assert client.stats["retries"] == 1
    assert client.stats["quota_units_estimated"] == 200


def test_read_timeout_retries_with_bounded_limit(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(200, json={"items": []})

    client = make_client(handler, monkeypatch, retries=2)
    assert client._get("channels", {"id": "UC1"}) == {"items": []}
    assert len(calls) == 3
    assert client.stats["retries"] == 2


def test_timeout_retries_are_bounded_and_actionable(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        raise httpx.ReadTimeout("timed out")

    client = make_client(handler, monkeypatch, retries=1)
    with pytest.raises(YouTubeAPIError, match="timed out after 2 attempts"):
        client._get("channels", {"id": "UC1"})
    assert len(calls) == 2


def test_successful_response_is_cached_and_quota_is_tracked(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json={"items": [{"id": {"channelId": "UC1"}}]})

    client = make_client(handler, monkeypatch)
    assert client.search_channels("AI", 10) == ["UC1"]
    assert client.search_channels("AI", 10) == ["UC1"]
    assert len(calls) == 1
    assert client.stats["request_count"] == 1
    assert client.stats["quota_units_estimated"] == 100
    second_client = make_client(handler, monkeypatch)
    assert second_client.search_channels("AI", 10) == ["UC1"]
    assert len(calls) == 1


def test_explicit_refresh_bypasses_cached_youtube_search(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        channel_id = f"UC-refresh-{len(calls)}"
        return httpx.Response(200, json={"items": [{"id": {"channelId": channel_id}}]})

    query = "unique refresh category for test"
    first = make_client(handler, monkeypatch)
    assert first.search_channels(query, 10) == ["UC-refresh-1"]

    refreshed = make_client(handler, monkeypatch)
    refreshed.bypass_search_cache = True
    assert refreshed.search_channels(query, 10) == ["UC-refresh-2"]
    assert len(calls) == 2


def test_video_enrichment_uses_playlist_and_batched_videos(monkeypatch):
    monkeypatch.setattr(yt_module, "get_settings", lambda: Settings(youtube_api_key="test", youtube_retry_backoff_seconds=0))
    client = YouTubeClient(api_key="test", client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"items": []}))))
    calls = []

    def get(endpoint, params):
        calls.append(endpoint)
        if endpoint == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": "vid-1"}}]}
        return {"items": [{"id": "vid-1", "snippet": {"title": "API tutorial"}, "statistics": {"viewCount": "100"}}]}

    monkeypatch.setattr(client, "_get", get)
    videos = client.recent_videos("channel-1", uploads_playlist_id="uploads-1")
    assert [video["id"] for video in videos] == ["vid-1"]
    assert calls == ["playlistItems", "videos"]


class PartialDiscoveryClient:
    key = "configured"
    stats = {"request_count": 4, "quota_units_estimated": 103, "retries": 1}

    def __init__(self):
        self.queries = []

    def search_channels(self, query, limit):
        self.queries.append(query)
        if len(self.queries) == 1:
            return ["UC-real-1", "UC-real-1"]
        raise YouTubeAPIError("YouTube API search failed (429; rateLimitExceeded).", status_code=429, reason="rateLimitExceeded")

    def channels(self, ids):
        assert ids == ["UC-real-1"]
        return [{"id": "UC-real-1", "snippet": {"title": "Real Channel", "description": "Python developer", "publishedAt": "2020-01-01T00:00:00Z"}, "statistics": {"subscriberCount": "12000", "videoCount": "40", "viewCount": "100000"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU-real-1"}}}]

    def recent_videos(self, channel_id, uploads_playlist_id=None, limit=10):
        assert channel_id == "UC-real-1"
        assert uploads_playlist_id == "UU-real-1"
        return [{"id": "video-real-1", "snippet": {"title": "Python tutorial", "publishedAt": "2026-11-01T00:00:00Z"}, "statistics": {"viewCount": "1000", "likeCount": "25", "commentCount": "5"}}]


def test_partial_discovery_is_preserved_and_rerun_does_not_duplicate(db_session, monkeypatch):
    monkeypatch.setattr("app.services.discovery.get_settings", lambda: Settings(youtube_search_query_limit=2))
    first = run_discovery(db_session, target_count=2, client=PartialDiscoveryClient())
    assert first["success"] is True
    assert first["discovered"] == 1
    assert first["stored"] == 1
    assert first["queries"][0]["status"] == "success"
    assert first["queries"][1]["status"] == "failed"
    assert db_session.scalar(select(func.count(Influencer.id))) == 1
    assert db_session.scalar(select(func.count(SourceEvidence.id))) == 1
    assert db_session.scalar(select(func.count(RecentVideo.id))) == 1

    second = run_discovery(db_session, target_count=2, client=PartialDiscoveryClient())
    assert second["stored"] == 1
    assert db_session.scalar(select(func.count(Influencer.id))) == 1
    assert db_session.scalar(select(func.count(SourceEvidence.id))) == 1
    assert db_session.scalar(select(func.count(RecentVideo.id))) == 1


def test_discovery_all_searches_failed_is_not_success(db_session, monkeypatch):
    monkeypatch.setattr("app.services.discovery.get_settings", lambda: Settings(youtube_search_query_limit=2))

    class BrokenClient(PartialDiscoveryClient):
        def search_channels(self, query, limit):
            self.queries.append(query)
            raise YouTubeAPIError("temporary API failure", status_code=500)

    result = run_discovery(db_session, target_count=1, client=BrokenClient())
    assert result["success"] is False
    assert result["stored"] == 0
    assert all(query["status"] == "failed" for query in result["queries"])


def test_discovery_stops_searching_immediately_on_daily_quota(db_session, monkeypatch):
    monkeypatch.setattr("app.services.discovery.get_settings", lambda: Settings(youtube_search_query_limit=3))

    class QuotaClient(PartialDiscoveryClient):
        def search_channels(self, query, limit):
            self.queries.append(query)
            if len(self.queries) == 1:
                return ["UC-real-1"]
            raise YouTubeQuotaExceeded("daily quota exhausted", status_code=403, reason="quotaExceeded")

        def channels(self, ids):
            raise AssertionError("No API calls should be made after quota exhaustion")

    client = QuotaClient()
    result = run_discovery(db_session, target_count=2, client=client)
    assert result["quota_exhausted"] is True
    assert result["success"] is True  # First query succeeded; second exhausted quota.
    assert len(client.queries) == 2
    assert result["queries"][2]["status"] == "skipped_quota_exhausted"


def test_fintech_niche_uses_fintech_queries_and_requested_target(db_session, monkeypatch):
    monkeypatch.setattr("app.services.discovery.get_settings", lambda: Settings(youtube_search_query_limit=3))

    class FintechClient:
        key = "configured"
        stats = {"request_count": 3, "quota_units_estimated": 102, "retries": 0}
        search_calls = []

        def search_channels(self, query, limit):
            self.search_calls.append((query, limit))
            return ["UC-finance-1", "UC-finance-2"]

        def channels(self, ids):
            assert ids == ["UC-finance-1", "UC-finance-2"]
            return [{"id": channel_id, "snippet": {"title": f"Fintech channel {index}", "description": "Fintech education and digital banking"}, "statistics": {"subscriberCount": "12000", "videoCount": "10", "viewCount": "100000"}, "contentDetails": {"relatedPlaylists": {"uploads": f"UU{index}"}}} for index, channel_id in enumerate(ids)]

        def recent_videos(self, channel_id, limit=10, uploads_playlist_id=None):
            return []

    client = FintechClient()
    result = run_discovery(db_session, target_count=2, niche="fintech", client=client)
    assert client.search_calls == [("fintech", 2)]
    assert result["target"] == 2
    assert result["discovered"] == 2
    assert result["stored"] == 2
    assert result["niche"] == "fintech"
    assert db_session.scalar(select(func.count(Influencer.id))) == 2
    assert {row.niche for row in db_session.execute(select(Influencer)).scalars()} == {"fintech"}

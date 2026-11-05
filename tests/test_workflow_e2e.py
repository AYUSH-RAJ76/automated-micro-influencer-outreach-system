"""End-to-end workflow check using mocked external providers and a local test DB."""
import json
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.models import Campaign, GeneratedMessage, Influencer, OutreachEvent
from app.services.discovery import run_discovery
from app.services.enrichment import enrich_influencer
from app.services.export import influencers_frame, messages_frame, outreach_frame
from app.services.personalization import generate
from app.services.sending import send_approved
from app.services.tracking import mark_dm


class MockYouTube:
    key = "mock-key"
    stats = {"request_count": 3, "quota_units_estimated": 103, "retries": 0}

    def search_channels(self, query, limit):
        assert query and limit == 1
        return ["UC-e2e"]

    def channels(self, channel_ids):
        assert channel_ids == ["UC-e2e"]
        return [{
            "id": "UC-e2e",
            "snippet": {"title": "Python Education Creator", "description": "Python and AI tutorials. Contact: creator@example.com", "publishedAt": "2020-01-01T00:00:00Z"},
            "statistics": {"subscriberCount": "12000", "videoCount": "3", "viewCount": "30000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-e2e"}},
        }]

    def recent_videos(self, channel_id, limit=10, uploads_playlist_id=None):
        assert channel_id == "UC-e2e"
        now = datetime.now(timezone.utc)
        return [{"id": f"video-{i}", "snippet": {"title": f"Python AI tutorial lesson {i}", "publishedAt": (now - timedelta(days=i)).isoformat()}, "statistics": {"viewCount": "1000", "likeCount": "30", "commentCount": "10"}} for i in range(3)]


def test_mocked_workflow_discovery_to_outreach_and_export(db_session, monkeypatch):
    from app.services import discovery, filtering, personalization, sending

    settings = Settings(
        youtube_search_query_limit=1,
        min_subscribers=5000,
        max_subscribers=100000,
        engagement_threshold=1,
        recent_days=180,
        groq_api_key="mock-groq-key",
        groq_model="openai/gpt-oss-120b",
        groq_max_retries=0,
        dry_run=True,
        send_delay_seconds=0,
    )
    monkeypatch.setattr(discovery, "get_settings", lambda: settings)
    monkeypatch.setattr(filtering, "get_settings", lambda: settings)
    monkeypatch.setattr(personalization, "get_settings", lambda: settings)
    monkeypatch.setattr(sending, "get_settings", lambda: settings)

    result = run_discovery(db_session, target_count=1, niche="Technology", client=MockYouTube())
    assert result["success"] and result["stored"] == 1
    influencer = db_session.scalar(__import__("sqlalchemy").select(Influencer))
    assert influencer.filter_status == "PASS"

    enrich_influencer(db_session, influencer)
    db_session.commit()
    assert influencer.email == "creator@example.com"

    email = " ".join(["developer collaboration tailored to your Python education content"] * 11)
    dm = "Hi, your Python tutorials look like a strong fit for our developer education campaign. Would you be open to collaborating with us?"

    class MockResponse:
        is_success = True

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"subject": "Python creator partnership", "email_body": email, "dm_body": dm})}}]}

    class MockGroq:
        def post(self, *_args, **_kwargs):
            return MockResponse()

    content, context = generate(influencer, client=MockGroq())
    assert context["recent_titles"]
    message = GeneratedMessage(influencer_id=influencer.id, subject=content["subject"], email_body=content["email_body"], dm_body=content["dm_body"], model_name=settings.groq_model, status="APPROVED")
    db_session.add(message)
    campaign = Campaign(name="E2E test campaign", brand_name="Test brand")
    db_session.add(campaign)
    db_session.commit()

    sent = send_approved(db_session, campaign, [message.id])
    assert sent == [{"message_id": message.id, "status": "SIMULATED"}]
    assert send_approved(db_session, campaign, [message.id])[0]["status"] == "SKIPPED_DUPLICATE"
    mark_dm(db_session, influencer.id, "SENT")

    assert len(influencers_frame(db_session)) == 1
    assert len(messages_frame(db_session)) == 1
    assert len(outreach_frame(db_session)) == 2
    assert db_session.query(OutreachEvent).filter_by(channel="instagram_dm", status="SENT").count() == 1

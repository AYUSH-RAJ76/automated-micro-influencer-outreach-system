from app.services.enrichment import valid_email, enrich_influencer
from app.services.personalization import words
from app.services.export import influencers_frame
from app.services.personalization import generate,PersonalizationError
from app.core.config import Settings

def test_email_validation_and_missing():
    assert valid_email("creator@example.com")
    assert not valid_email("creator@@example")
    assert not valid_email("Not Found")

def test_enrichment_extracts_only_public_contact_data(db_session):
    import json
    from app.db.models import Influencer
    creator=Influencer(channel_id="UC-contact",name="Creator",profile_url="https://youtube.com/channel/UC-contact",description="Business: creator@example.com Visit https://creator.example/contact. Social: https://instagram.com/creator")
    db_session.add(creator);db_session.flush()
    enrich_influencer(db_session,creator)
    flags=json.loads(creator.data_flags)
    assert creator.email=="creator@example.com"
    assert creator.email_source==creator.profile_url
    assert flags["public_contact_links"]==["https://creator.example/contact","https://instagram.com/creator"]

def test_word_count():
    assert words("one two three")==3

def test_missing_llm_credentials(monkeypatch):
    monkeypatch.setattr("app.services.personalization.get_settings",lambda:Settings(groq_api_key=""))
    class Inf:name="Creator"; niche="Technology";videos=[];themes="[]";content_style=None
    try:generate(Inf())
    except PersonalizationError as e:assert "GROQ_API_KEY" in str(e)
    else:assert False

def test_groq_generation_sends_configured_model_and_parses_json(monkeypatch):
    import json
    from app.core.config import Settings
    monkeypatch.setattr("app.services.personalization.get_settings",lambda:Settings(groq_api_key="test-key",groq_model="openai/gpt-oss-120b",groq_max_retries=0))
    email=" ".join(["developer collaboration tailored to your Python education content"]*11)
    dm="Hi, your Python tutorials look like a strong fit for an illustrative developer productivity collaboration. Open to details?"
    assert 60 <= words(email) <= 90
    assert 15 <= words(dm) <= 30
    class Response:
        is_success=True
        def json(self):return {"choices":[{"message":{"content":json.dumps({"subject":"A possible collaboration","email_body":email,"dm_body":dm})}}]}
    class Client:
        def post(self,url,headers,json):
            assert url=="https://api.groq.com/openai/v1/chat/completions"
            assert headers["Authorization"]=="Bearer test-key"
            assert json["model"]=="openai/gpt-oss-120b"
            assert json["response_format"]["type"]=="json_schema"
            assert json["response_format"]["json_schema"]["strict"] is True
            assert json["response_format"]["json_schema"]["schema"]["required"]==["subject","email_body","dm_body"]
            return Response()
    class Influencer:
        name="Creator";niche="Technology";videos=[];themes="Python education";content_style="Tutorials"
    result,context=generate(Influencer(),client=Client())
    assert result["subject"]=="A possible collaboration"
    assert context["name"]=="Creator"

def test_groq_schema_error_includes_provider_failed_generation(monkeypatch):
    from app.core.config import Settings
    from app.services.personalization import _post_with_retries, PersonalizationError
    monkeypatch.setattr("app.services.personalization.get_settings",lambda:Settings(groq_api_key="test-key",groq_max_retries=0))
    class Response:
        is_success=False
        status_code=400
        def json(self):return {"error":{"message":"Failed to generate JSON.","failed_generation":"truncated output"}}
    class Client:
        def post(self,*args,**kwargs):return Response()
    try:
        _post_with_retries(Client(),{},Settings(groq_api_key="test-key",groq_max_retries=0))
    except PersonalizationError as exc:
        assert "truncated output" in str(exc)
    else:assert False

def test_groq_timeout_is_retried_then_reported(monkeypatch):
    import httpx
    from app.core.config import Settings
    monkeypatch.setattr("app.services.personalization.get_settings",lambda:Settings(groq_api_key="test-key",groq_max_retries=1,groq_retry_backoff_seconds=0))
    monkeypatch.setattr("app.services.personalization.time.sleep",lambda _delay:None)
    class Client:
        calls=0
        def post(self,*args,**kwargs):
            self.calls+=1
            raise httpx.ReadTimeout("mock timeout")
    class Influencer:
        name="Creator";niche="Technology";videos=[];themes="[]";content_style=None
    client=Client()
    try:generate(Influencer(),client=client)
    except PersonalizationError as e:assert "timed out after 2 attempt(s)" in str(e)
    else:assert False
    assert client.calls==2

def test_groq_generation_loads_recent_videos_from_active_session(db_session,monkeypatch):
    import json
    from app.core.config import Settings
    from app.db.models import Influencer,RecentVideo
    monkeypatch.setattr("app.services.personalization.get_settings",lambda:Settings(groq_api_key="test-key",groq_max_retries=0))
    email=" ".join(["developer collaboration tailored to your Python education content"]*11)
    dm="Hi, your Python tutorials look like a strong fit for an illustrative developer productivity collaboration. Open to details?"
    class Response:
        is_success=True
        def json(self):return {"choices":[{"message":{"content":json.dumps({"subject":"Possible partnership","email_body":email,"dm_body":dm})}}]}
    class Client:
        def post(self,*args,**kwargs):return Response()
    influencer=Influencer(channel_id="UC-session",name="Python Creator",profile_url="https://youtube.com/channel/UC-session")
    db_session.add(influencer);db_session.flush()
    db_session.add(RecentVideo(influencer_id=influencer.id,video_id="video-session",title="Python typing tutorial",url="https://youtube.com/watch?v=video-session"));db_session.commit()
    selected_id=influencer.id
    # Mimic the dashboard's selected-ID pattern: reload the ORM row in this active session.
    reloaded=db_session.get(Influencer,selected_id)
    result,context=generate(reloaded,client=Client())
    assert result["subject"]=="Possible partnership"
    assert context["recent_titles"]==["Python typing tutorial"]

def test_export_empty_schema(db_session):
    frame=influencers_frame(db_session)
    assert "profile_url" in frame.columns and "video_engagement_per_view_pct" in frame.columns

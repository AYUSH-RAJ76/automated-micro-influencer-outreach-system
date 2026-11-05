from fastapi import HTTPException
from pydantic import ValidationError
from app.api.main import health,discovery
from app.schemas.api import DiscoveryRequest
from app.services.sending import send_approved
from app.db.models import Campaign,Influencer,GeneratedMessage

def test_health():assert health()["status"]=="ok"

def test_missing_youtube_key_returns_actionable_error(monkeypatch):
    monkeypatch.setattr("app.api.main.get_settings",lambda:type("S",(),{"youtube_api_key":""})())
    try:discovery(DiscoveryRequest(target_count=5,niche="Technology"))
    except HTTPException as e:assert e.status_code==503
    else:assert False

def test_discovery_api_rejects_removed_instagram_platform():
    try:DiscoveryRequest(target_count=5,niche="Technology",platform="Instagram")
    except ValidationError:pass
    else:assert False, "Removed discovery platforms must not be silently treated as YouTube"

def test_approval_gate_and_dry_run(db_session,monkeypatch):
    from app.core.config import Settings
    monkeypatch.setattr("app.services.sending.get_settings",lambda:Settings(dry_run=True))
    i=Influencer(channel_id="x",name="X",profile_url="https://youtube.com/channel/x",email="x@example.com");db_session.add(i);db_session.flush()
    c=Campaign(name="c");db_session.add(c);db_session.flush()
    m=GeneratedMessage(influencer_id=i.id,campaign_id=c.id,status="DRAFT",subject="hello",email_body="hello");db_session.add(m);db_session.commit()
    assert send_approved(db_session,c,[m.id])[0]["status"]=="NEEDS_REVIEW"
    m.status="APPROVED";db_session.commit()
    assert send_approved(db_session,c,[m.id])[0]["status"]=="SIMULATED"
    assert send_approved(db_session,c,[m.id])[0]["status"]=="SKIPPED_DUPLICATE"

def test_uncertain_attempt_requires_review(db_session,monkeypatch):
    from app.core.config import Settings
    from app.db.models import OutreachAttempt
    monkeypatch.setattr("app.services.sending.get_settings",lambda:Settings(dry_run=True))
    i=Influencer(channel_id="y",name="Y",profile_url="https://youtube.com/channel/y",email="y@example.com");db_session.add(i);db_session.flush()
    c=Campaign(name="c");db_session.add(c);db_session.flush()
    m=GeneratedMessage(influencer_id=i.id,campaign_id=c.id,status="APPROVED",subject="hello",email_body="hello");db_session.add(m);db_session.flush()
    db_session.add(OutreachAttempt(influencer_id=i.id,campaign_id=c.id,message_id=m.id,normalized_email=i.email,status="SENDING"));db_session.commit()
    assert send_approved(db_session,c,[m.id])[0]["status"]=="NEEDS_REVIEW"

def test_test_email_requires_confirmation_and_uses_only_given_recipient(monkeypatch):
    from app.core.config import Settings
    from app.services.sending import send_test_email
    monkeypatch.setattr("app.services.sending.get_settings",lambda:Settings(smtp_host="smtp.test",smtp_port=587,smtp_user="user",smtp_password="password",smtp_from="sender@example.test"))

    class SMTP:
        mail=None
        def __init__(self,host,port,timeout):
            assert (host,port,timeout)==("smtp.test",587,20)
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def starttls(self):pass
        def login(self,user,password):assert (user,password)==("user","password")
        def send_message(self,mail):SMTP.mail=mail

    result=send_test_email("recipient@example.test",smtp_factory=SMTP)
    assert result["status"]=="NOT_SENT"
    assert SMTP.mail is None
    result=send_test_email("recipient@example.test",live_confirmed=True,smtp_factory=SMTP)
    assert result["status"]=="SENT"
    assert SMTP.mail["To"]=="recipient@example.test"
    assert "No influencer outreach was sent" in SMTP.mail.get_content()

def test_test_email_rejects_invalid_recipient(monkeypatch):
    from app.core.config import Settings
    from app.services.sending import send_test_email
    monkeypatch.setattr("app.services.sending.get_settings",lambda:Settings(smtp_host="smtp.test",smtp_user="user",smtp_password="password",smtp_from="sender@example.test"))
    try:send_test_email("not-an-email",live_confirmed=True)
    except ValueError as exc:assert "valid recipient" in str(exc)
    else:assert False

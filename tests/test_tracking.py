import pytest
from sqlalchemy import select

from app.api.main import logs
from app.db.models import Campaign, GeneratedMessage, Influencer, OutreachAttempt, OutreachEvent
from app.services.export import outreach_frame
from app.services.tracking import mark_dm, record_outreach_event


def test_dm_tracking_and_email_followup_store_named_events(db_session):
    creator=Influencer(channel_id="UC-tracker",name="Tracker Creator",profile_url="https://youtube.com/channel/UC-tracker",filter_status="PASS")
    campaign=Campaign(name="Tracker campaign")
    db_session.add_all([creator,campaign]);db_session.commit()

    dm=mark_dm(db_session,creator.id,"SENT",note="Sent the approved DM manually",campaign_id=campaign.id)
    reply=record_outreach_event(db_session,creator.id,"email","REPLIED",note="Asked for details",campaign_id=campaign.id)

    assert dm.channel=="instagram_dm" and dm.status=="SENT"
    assert dm.note=="Sent the approved DM manually"
    assert reply.channel=="email" and reply.status=="REPLIED"
    assert reply.campaign_id==campaign.id
    frame=outreach_frame(db_session)
    assert len(frame)==2
    assert set(frame["creator_name"])=={"Tracker Creator"}


def test_outreach_api_returns_creator_and_campaign_names(db_session):
    creator=Influencer(channel_id="UC-api-tracker",name="API Tracker Creator",profile_url="https://youtube.com/channel/UC-api-tracker")
    campaign=Campaign(name="API tracker campaign")
    db_session.add_all([creator,campaign]);db_session.flush()
    message=GeneratedMessage(influencer_id=creator.id,campaign_id=campaign.id,status="SENT",subject="Test",email_body="Sent")
    db_session.add(message);db_session.flush()
    db_session.add(OutreachAttempt(influencer_id=creator.id,campaign_id=campaign.id,message_id=message.id,normalized_email="creator@example.test",status="SENT"))
    db_session.add(OutreachEvent(influencer_id=creator.id,campaign_id=campaign.id,channel="instagram_dm",status="SENT",note="Sent manually"))
    db_session.commit()

    rows=logs(db_session)
    assert {row["channel"] for row in rows}=={"email","instagram_dm"}
    assert all(row["creator_name"]=="API Tracker Creator" for row in rows)
    assert all(row["campaign_name"]=="API tracker campaign" for row in rows)


@pytest.mark.parametrize("channel,status",[("sms","REPLIED"),("email","SENT"),("email","BOGUS")])
def test_tracking_rejects_invalid_manual_events(db_session,channel,status):
    creator=Influencer(channel_id=f"UC-invalid-{channel}-{status}",name="Creator",profile_url="https://youtube.com/channel/example")
    db_session.add(creator);db_session.commit()
    with pytest.raises(ValueError):
        record_outreach_event(db_session,creator.id,channel,status)

from app.db.models import Campaign, Influencer, OutreachEvent


def record_outreach_event(db,influencer_id,channel,status,note=None,campaign_id=None):
    if channel not in {"email","instagram_dm"}:
        raise ValueError("Channel must be email or instagram_dm")
    if status not in {"REPLIED","FOLLOW_UP","SKIPPED"}:
        raise ValueError("Activity must be REPLIED, FOLLOW_UP, or SKIPPED")
    if db.get(Influencer,influencer_id) is None:
        raise ValueError("Influencer does not exist")
    if campaign_id is not None and db.get(Campaign,campaign_id) is None:
        raise ValueError("Campaign does not exist")
    event=OutreachEvent(influencer_id=influencer_id,campaign_id=campaign_id,channel=channel,status=status,note=(note or "").strip() or None)
    db.add(event);db.commit();return event


def mark_dm(db,influencer_id,status,note=None,campaign_id=None):
    if status not in {"SENT","SKIPPED"}:
        raise ValueError("DM status must be SENT or SKIPPED")
    if db.get(Influencer,influencer_id) is None:
        raise ValueError("Influencer does not exist")
    if campaign_id is not None and db.get(Campaign,campaign_id) is None:
        raise ValueError("Campaign does not exist")
    event=OutreachEvent(influencer_id=influencer_id,campaign_id=campaign_id,channel="instagram_dm",status=status,note=(note or "").strip() or None)
    db.add(event);db.commit();return event

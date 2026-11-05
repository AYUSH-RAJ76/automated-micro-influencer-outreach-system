import json
from sqlalchemy import select
import pandas as pd
from app.db.models import Influencer, GeneratedMessage, OutreachAttempt, OutreachEvent, Campaign

def influencers_frame(db,qualified=False):
    q=select(Influencer)
    if qualified:q=q.where(Influencer.filter_status=="PASS")
    rows=db.execute(q).scalars().all()
    columns=["id","name","channel_id","profile_url","subscribers","video_engagement_per_view_pct","engagement_status","niche","filter_status","reason_codes","email","email_source","website","public_contact_links","country","collected_at"]
    def contact_links(influencer):
        try:return json.loads(influencer.data_flags or "{}").get("public_contact_links",[])
        except (TypeError,ValueError):return []
    return pd.DataFrame([{"id":i.id,"name":i.name,"channel_id":i.channel_id,"profile_url":i.profile_url,"subscribers":i.subscribers,"video_engagement_per_view_pct":i.engagement_rate,"engagement_status":i.engagement_status,"niche":i.niche,"filter_status":i.filter_status,"reason_codes":i.reason_codes,"email":i.email,"email_source":i.email_source,"website":i.website,"public_contact_links":" | ".join(contact_links(i)),"country":i.country,"collected_at":i.collected_at.isoformat() if i.collected_at else None} for i in rows],columns=columns)

def messages_frame(db):
    rows=db.execute(select(GeneratedMessage)).scalars().all()
    columns=["id","influencer_id","campaign_id","subject","email_body","dm_body","status","model"]
    return pd.DataFrame([{"id":m.id,"influencer_id":m.influencer_id,"campaign_id":m.campaign_id,"subject":m.subject,"email_body":m.email_body,"dm_body":m.dm_body,"status":m.status,"model":m.model_name} for m in rows],columns=columns)

def outreach_frame(db):
    rows=db.execute(select(OutreachAttempt)).scalars().all()
    columns=["record_type","id","influencer_id","creator_name","campaign_id","campaign_name","channel","email","status","attempted_at","details"]
    records=[]
    for attempt in rows:
        influencer=db.get(Influencer,attempt.influencer_id);campaign=db.get(Campaign,attempt.campaign_id)
        records.append({"record_type":"email_attempt","id":attempt.id,"influencer_id":attempt.influencer_id,"creator_name":influencer.name if influencer else None,"campaign_id":attempt.campaign_id,"campaign_name":campaign.name if campaign else None,"channel":"email","email":attempt.normalized_email,"status":attempt.status,"attempted_at":attempt.attempted_at.isoformat() if attempt.attempted_at else None,"details":attempt.error or attempt.provider_response})
    duplicate_send_event= (OutreachEvent.channel=="email") & (OutreachEvent.status.in_(["SENDING","SENT","SIMULATED","FAILED"]))
    events=db.execute(select(OutreachEvent).where(~duplicate_send_event)).scalars().all()
    for event in events:
        influencer=db.get(Influencer,event.influencer_id);campaign=db.get(Campaign,event.campaign_id) if event.campaign_id else None
        records.append({"record_type":"manual_activity","id":event.id,"influencer_id":event.influencer_id,"creator_name":influencer.name if influencer else None,"campaign_id":event.campaign_id,"campaign_name":campaign.name if campaign else None,"channel":event.channel,"email":None,"status":event.status,"attempted_at":event.created_at.isoformat() if event.created_at else None,"details":event.note})
    return pd.DataFrame(records,columns=columns)

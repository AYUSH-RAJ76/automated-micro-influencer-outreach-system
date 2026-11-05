import json, logging, uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.db.database import Base, engine, get_db, SessionLocal
from app.db.models import Influencer, RecentVideo, GeneratedMessage, Campaign, Job, Approval, OutreachAttempt, OutreachEvent
from app.schemas.api import DiscoveryRequest, CampaignRequest, MessageUpdate, SendRequest
from app.services.discovery import run_discovery
from app.services.filtering import save_decision
from app.services.enrichment import enrich_influencer
from app.services.personalization import generate, PersonalizationError
from app.services.sending import send_approved
from app.services.export import influencers_frame, messages_frame, outreach_frame
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO);log=logging.getLogger(__name__)
Base.metadata.create_all(bind=engine)
app=FastAPI(title="Automated Micro-Influencer Outreach System",version="1.0.0")
pool=ThreadPoolExecutor(max_workers=2)

def job_work(job_id,kind,args):
    with SessionLocal() as db:
        job=db.get(Job,job_id);job.status="RUNNING";job.progress=5;db.commit()
        try:
            if kind=="discovery":result=run_discovery(db,**args)
            elif kind=="personalization":
                ids=args["ids"];result={"generated":0,"errors":[]}
                for idx,iid in enumerate(ids):
                    inf=db.get(Influencer,iid)
                    if not inf or inf.filter_status!="PASS":result["errors"].append(f"{iid}: not qualified");continue
                    try:
                        data,context=generate(inf);msg=GeneratedMessage(influencer_id=inf.id,subject=data["subject"],email_body=data["email_body"],dm_body=data["dm_body"],model_name=get_settings().groq_model,source_context=json.dumps(context));db.add(msg);db.commit();result["generated"]+=1
                    except Exception as e:result["errors"].append(f"{iid}: {e}")
                    job.progress=min(95,5+int(90*(idx+1)/max(len(ids),1)));db.commit()
            job.status="FAILED" if kind=="discovery" and not result.get("success",False) else "COMPLETE";job.progress=100;job.result=json.dumps(result);db.commit()
        except Exception as e:
            log.exception("Job %s failed",job_id);job.status="FAILED";job.error=str(e);db.commit()

def enqueue(kind,args):
    jid=str(uuid.uuid4())
    with SessionLocal() as db:db.add(Job(id=jid,kind=kind));db.commit()
    pool.submit(job_work,jid,kind,args)
    return {"job_id":jid,"status":"QUEUED","status_url":f"/api/jobs/{jid}"}

@app.get("/health")
def health():return {"status":"ok","youtube_api_key_configured":bool(get_settings().youtube_api_key),"groq_api_key_configured":bool(get_settings().groq_api_key)}

@app.post("/api/discovery/run",status_code=202)
def discovery(req:DiscoveryRequest):
    if not get_settings().youtube_api_key:raise HTTPException(503,"YOUTUBE_API_KEY is missing. Configure it in .env for real discovery.")
    return enqueue("discovery",{"target_count":req.target_count,"niche":req.niche})

@app.get("/api/jobs/{job_id}")
def job_status(job_id:str,db:Session=Depends(get_db)):
    j=db.get(Job,job_id)
    if not j:raise HTTPException(404,"Job not found")
    return {"job_id":j.id,"kind":j.kind,"status":j.status,"progress":j.progress,"result":json.loads(j.result or "{}"),"error":j.error}

@app.get("/api/influencers")
def list_influencers(status:str|None=None,search:str|None=None,limit:int=100,offset:int=0,db:Session=Depends(get_db)):
    q=select(Influencer)
    if status:q=q.where(Influencer.filter_status==status.upper())
    if search:q=q.where(Influencer.name.ilike(f"%{search}%"))
    rows=db.execute(q.order_by(Influencer.subscribers.desc()).offset(offset).limit(min(limit,500))).scalars().all()
    return [{"id":i.id,"name":i.name,"channel_id":i.channel_id,"platform":i.platform,"profile_url":i.profile_url,"subscribers":i.subscribers,"engagement_rate":i.engagement_rate,"engagement_status":i.engagement_status,"niche":i.niche,"filter_status":i.filter_status,"reason_codes":json.loads(i.reason_codes),"reason_text":i.reason_text,"email":i.email,"email_source":i.email_source,"do_not_contact":i.do_not_contact,"video_count":len(i.videos)} for i in rows]

@app.get("/api/influencers/{influencer_id}")
def influencer_detail(influencer_id:int,db:Session=Depends(get_db)):
    i=db.get(Influencer,influencer_id)
    if not i:raise HTTPException(404,"Influencer not found")
    return {"id":i.id,"name":i.name,"channel_id":i.channel_id,"platform":i.platform,"profile_url":i.profile_url,"subscribers":i.subscribers,"video_count":i.video_count,"view_count":i.view_count,"description":i.description,"country":i.country,"email":i.email,"email_source":i.email_source,"website":i.website,"themes":json.loads(i.themes),"content_style":i.content_style,"engagement_rate":i.engagement_rate,"engagement_status":i.engagement_status,"filter_status":i.filter_status,"reason_codes":json.loads(i.reason_codes),"reason_text":i.reason_text,"videos":[{"title":v.title,"published_at":v.published_at,"views":v.views,"likes":v.likes,"comments":v.comments,"url":v.url} for v in i.videos]}

@app.post("/api/filter/run")
def filter_run(db:Session=Depends(get_db)):
    rows=db.execute(select(Influencer)).scalars().all()
    for i in rows:save_decision(db,i,list(i.videos))
    db.commit();return {"processed":len(rows),"qualified":sum(i.filter_status=="PASS" for i in rows)}

@app.post("/api/enrichment/run")
def enrichment(ids:list[int]|None=None,db:Session=Depends(get_db)):
    q=select(Influencer).where(Influencer.filter_status=="PASS")
    if ids:q=q.where(Influencer.id.in_(ids))
    rows=db.execute(q).scalars().all()
    for i in rows:enrich_influencer(db,i)
    db.commit();return {"enriched":len(rows)}

@app.post("/api/personalization/run",status_code=202)
def personalization(ids:list[int],db:Session=Depends(get_db)):
    if not get_settings().groq_api_key:raise HTTPException(503,"GROQ_API_KEY is missing. Configure it to generate real AI messages.")
    if not ids:raise HTTPException(422,"Provide at least one influencer id")
    return enqueue("personalization",{"ids":ids})

@app.get("/api/messages")
def list_messages(status:str|None=None,db:Session=Depends(get_db)):
    q=select(GeneratedMessage)
    if status:q=q.where(GeneratedMessage.status==status.upper())
    return [{"id":m.id,"influencer_id":m.influencer_id,"influencer_name":m.influencer.name,"campaign_id":m.campaign_id,"subject":m.subject,"email_body":m.email_body,"dm_body":m.dm_body,"status":m.status,"model_name":m.model_name} for m in db.execute(q.order_by(GeneratedMessage.created_at.desc())).scalars()]

@app.patch("/api/messages/{message_id}")
def edit_message(message_id:int,req:MessageUpdate,db:Session=Depends(get_db)):
    m=db.get(GeneratedMessage,message_id)
    if not m:raise HTTPException(404,"Message not found")
    for k,v in req.model_dump(exclude_unset=True).items():setattr(m,k,v)
    if m.status in {"APPROVED","REJECTED"}:m.status="DRAFT"
    db.commit();return {"id":m.id,"status":m.status,"subject":m.subject,"email_body":m.email_body,"dm_body":m.dm_body}

@app.post("/api/messages/{message_id}/approve")
def approve(message_id:int,db:Session=Depends(get_db)):
    m=db.get(GeneratedMessage,message_id)
    if not m:raise HTTPException(404,"Message not found")
    inf=db.get(Influencer,m.influencer_id)
    if inf.do_not_contact:raise HTTPException(409,"Influencer is marked do-not-contact")
    m.status="APPROVED";m.approved_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc);db.add(Approval(message_id=m.id,decision="APPROVED"));db.commit();return {"id":m.id,"status":m.status}

@app.post("/api/messages/{message_id}/reject")
def reject(message_id:int,db:Session=Depends(get_db)):
    m=db.get(GeneratedMessage,message_id)
    if not m:raise HTTPException(404,"Message not found")
    m.status="REJECTED";db.add(Approval(message_id=m.id,decision="REJECTED"));db.commit();return {"id":m.id,"status":m.status}

@app.patch("/api/influencers/{influencer_id}/do-not-contact")
def dnc(influencer_id:int,enabled:bool=True,db:Session=Depends(get_db)):
    i=db.get(Influencer,influencer_id)
    if not i:raise HTTPException(404,"Influencer not found")
    i.do_not_contact=enabled;db.commit();return {"id":i.id,"do_not_contact":i.do_not_contact}

@app.post("/api/campaigns")
def create_campaign(req:CampaignRequest,db:Session=Depends(get_db)):
    c=Campaign(name=req.name,brand_name=req.brand_name);db.add(c);db.commit();db.refresh(c);return {"id":c.id,"name":c.name,"brand_name":c.brand_name}

@app.post("/api/campaigns/{campaign_id}/send")
def send(campaign_id:int,req:SendRequest,db:Session=Depends(get_db)):
    c=db.get(Campaign,campaign_id)
    if not c:raise HTTPException(404,"Campaign not found")
    try:return {"results":send_approved(db,c,req.message_ids,req.live_confirmed)}
    except ValueError as e:raise HTTPException(409,str(e))

@app.get("/api/outreach/logs")
def logs(db:Session=Depends(get_db)):
    attempts=db.execute(select(OutreachAttempt).order_by(OutreachAttempt.id.desc())).scalars().all()
    timeline=[]
    for attempt in attempts:
        influencer=db.get(Influencer,attempt.influencer_id)
        campaign=db.get(Campaign,attempt.campaign_id)
        timeline.append({"record_type":"email_attempt","id":attempt.id,"influencer_id":attempt.influencer_id,"creator_name":influencer.name if influencer else None,"campaign_id":attempt.campaign_id,"campaign_name":campaign.name if campaign else None,"channel":"email","email":attempt.normalized_email,"status":attempt.status,"attempted_at":attempt.attempted_at,"details":attempt.error or attempt.provider_response})
    duplicate_send_event=(OutreachEvent.channel=="email") & (OutreachEvent.status.in_(["SENDING","SENT","SIMULATED","FAILED"]))
    events=db.execute(select(OutreachEvent).where(~duplicate_send_event).order_by(OutreachEvent.id.desc())).scalars().all()
    for event in events:
        influencer=db.get(Influencer,event.influencer_id)
        campaign=db.get(Campaign,event.campaign_id) if event.campaign_id else None
        timeline.append({"record_type":"manual_activity","id":event.id,"influencer_id":event.influencer_id,"creator_name":influencer.name if influencer else None,"campaign_id":event.campaign_id,"campaign_name":campaign.name if campaign else None,"channel":event.channel,"email":None,"status":event.status,"attempted_at":event.created_at,"details":event.note})
    return sorted(timeline,key=lambda row:row["attempted_at"].isoformat() if row["attempted_at"] else "",reverse=True)

def csv_response(frame,name):return Response(frame.to_csv(index=False),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename={name}"})
@app.get("/api/export/influencers")
def export_influencers(qualified:bool=False,db:Session=Depends(get_db)):return csv_response(influencers_frame(db,qualified),"influencers.csv")
@app.get("/api/export/messages")
def export_messages(db:Session=Depends(get_db)):return csv_response(messages_frame(db),"messages.csv")
@app.get("/api/export/outreach")
def export_outreach(db:Session=Depends(get_db)):return csv_response(outreach_frame(db),"outreach.csv")

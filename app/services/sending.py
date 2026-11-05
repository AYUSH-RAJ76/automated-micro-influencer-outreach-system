import smtplib, time
from email.message import EmailMessage
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.config import get_settings
from app.db.models import OutreachAttempt, OutreachEvent
from app.services.enrichment import valid_email


def _smtp_send(mail, settings, smtp_factory=None):
    factory=smtp_factory or smtplib.SMTP
    with factory(settings.smtp_host,settings.smtp_port,timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user,settings.smtp_password)
        smtp.send_message(mail)


def send_test_email(recipient, live_confirmed=False, smtp_factory=None):
    """Send one neutral test email to an explicitly supplied address."""
    recipient=(recipient or "").strip()
    if not valid_email(recipient):
        raise ValueError("Enter a valid recipient email address for the test.")
    settings=get_settings()
    if not all((settings.smtp_host,settings.smtp_user,settings.smtp_password,settings.smtp_from)):
        raise ValueError("SMTP settings are incomplete. Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM.")
    if not live_confirmed:
        return {"status":"NOT_SENT","message":"Check the confirmation box to send the test email."}
    mail=EmailMessage()
    mail["Subject"]="Creator Outreach SMTP test"
    mail["From"]=settings.smtp_from
    mail["To"]=recipient
    mail.set_content("This is a test email from the Creator Outreach campaign sender. No influencer outreach was sent.")
    try:
        _smtp_send(mail,settings,smtp_factory)
    except Exception as exc:
        # Avoid returning SMTP connection details or credential-adjacent data.
        raise RuntimeError(f"SMTP could not send the test email ({type(exc).__name__}). Check SMTP settings and provider access.") from exc
    return {"status":"SENT","message":"SMTP accepted the test email."}

def send_approved(db,campaign,message_ids,live_confirmed=False):
    s=get_settings();out=[]
    if not s.dry_run and not live_confirmed:raise ValueError("Live bulk sending requires explicit confirmation.")
    if not s.dry_run and not (s.smtp_host and s.smtp_user and s.smtp_password and s.smtp_from):raise ValueError("SMTP credentials are incomplete.")
    rows=db.execute(select(__import__('app.db.models',fromlist=['GeneratedMessage']).GeneratedMessage).where(__import__('app.db.models',fromlist=['GeneratedMessage']).GeneratedMessage.id.in_(message_ids))).scalars().all()
    for message in rows[:s.send_limit]:
        inf=message.influencer if hasattr(message,"influencer") else db.get(__import__('app.db.models',fromlist=['Influencer']).Influencer,message.influencer_id)
        email=(inf.email or "").strip().lower()
        if message.status in {"SENT","SIMULATED"}: status="SKIPPED_DUPLICATE"
        elif message.status!="APPROVED": status="NEEDS_REVIEW"
        elif inf.do_not_contact:status="DO_NOT_CONTACT"
        elif not valid_email(email) or email=="not found":status="NEEDS_REVIEW"
        elif (existing:=db.scalar(select(OutreachAttempt).where(OutreachAttempt.normalized_email==email,OutreachAttempt.campaign_id==campaign.id))):
            # An interrupted SENDING attempt has an uncertain delivery outcome. Require manual review.
            status="NEEDS_REVIEW" if existing.status in {"SENDING","FAILED"} else "SKIPPED_DUPLICATE"
        else:
            attempt=OutreachAttempt(influencer_id=inf.id,campaign_id=campaign.id,message_id=message.id,normalized_email=email,status="SENDING",attempted_at=datetime.now(timezone.utc));db.add(attempt);db.commit()
            if s.dry_run:status="SIMULATED"
            else:
                try:
                    mail=EmailMessage();mail["Subject"]=message.subject;mail["From"]=s.smtp_from;mail["To"]=email;mail.set_content(message.email_body)
                    _smtp_send(mail,s);status="SENT";attempt.provider_response="SMTP accepted"
                except Exception as e:status="FAILED";attempt.error=str(e)
            attempt.status=status;message.status=status;db.add(OutreachEvent(influencer_id=inf.id,campaign_id=campaign.id,channel="email",status=status));db.commit();time.sleep(s.send_delay_seconds)
        out.append({"message_id":message.id,"status":status})
    return out

from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base

def now(): return datetime.now(timezone.utc)

class Influencer(Base):
    __tablename__ = "influencers"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    platform: Mapped[str] = mapped_column(String(30), default="YouTube")
    profile_url: Mapped[str] = mapped_column(String(500))
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    niche: Mapped[str] = mapped_column(String(100), default="Technology")
    email: Mapped[str] = mapped_column(String(320), default="Not Found")
    email_source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    engagement_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    filter_status: Mapped[str] = mapped_column(String(20), default="NEEDS_REVIEW")
    reason_codes: Mapped[str] = mapped_column(Text, default="[]")
    reason_text: Mapped[str] = mapped_column(Text, default="")
    themes: Mapped[str] = mapped_column(Text, default="[]")
    content_style: Mapped[str | None] = mapped_column(String(250), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="YouTube Data API v3")
    data_flags: Mapped[str] = mapped_column(Text, default="{}")
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    videos: Mapped[list["RecentVideo"]] = relationship(back_populates="influencer", cascade="all, delete-orphan")

class RecentVideo(Base):
    __tablename__ = "recent_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics_available: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(500))
    influencer: Mapped[Influencer] = relationship(back_populates="videos")

class SourceEvidence(Base):
    __tablename__ = "source_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class EnrichmentRecord(Base):
    __tablename__ = "enrichment_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="COMPLETE")
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class FilteringDecision(Base):
    __tablename__ = "filtering_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    reason_codes: Mapped[str] = mapped_column(Text)
    reasons: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    brand_name: Mapped[str] = mapped_column(String(200), default="DevFlow AI")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class GeneratedMessage(Base):
    __tablename__ = "generated_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    email_body: Mapped[str] = mapped_column(Text, default="")
    dm_body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_context: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    influencer: Mapped[Influencer] = relationship()

class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("generated_messages.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(100), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class OutreachAttempt(Base):
    __tablename__ = "outreach_attempts"
    __table_args__ = (UniqueConstraint("normalized_email", "campaign_id", name="uq_outreach_email_campaign"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("generated_messages.id"))
    normalized_email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)

class OutreachEvent(Base):
    __tablename__ = "outreach_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(ForeignKey("influencers.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

Index("ix_video_influencer_date", RecentVideo.influencer_id, RecentVideo.published_at)

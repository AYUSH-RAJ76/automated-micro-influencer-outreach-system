"""Sequential, resumable discovery with partial commits and explicit query outcomes."""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Influencer, RecentVideo, SourceEvidence
from app.services.filtering import save_decision
from app.services.youtube_client import queries_for_niche, YouTubeQuotaExceeded, YouTubeClient

log = logging.getLogger(__name__)


def parse_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_quota_error(exc: Exception) -> bool:
    return isinstance(exc, YouTubeQuotaExceeded) or getattr(exc, "reason", None) == "quotaExceeded"


def run_discovery(db, target_count=50, niche="Technology", client=None, refresh_search_cache=False):
    yt = client or YouTubeClient()
    # Keep normal repeated searches cached to save quota, but let users explicitly
    # ask YouTube for current results from the Discovery page.
    yt.bypass_search_cache = bool(refresh_search_cache)
    settings = get_settings()
    candidates: list[str] = []
    seen: set[str] = set()
    query_results: list[dict] = []
    errors: list[str] = []
    quota_exhausted = False

    # The requested count is a strict cap. Search uses the selected niche, and
    # further queries only fill slots when earlier results were duplicates/few.
    candidate_limit = max(1, min(100, int(target_count)))
    niche_queries = queries_for_niche(niche)
    query_limit = max(1, min(len(niche_queries), settings.youtube_search_query_limit))
    for query in niche_queries[:query_limit]:
        if len(candidates) >= candidate_limit:
            break
        try:
            found = yt.search_channels(query, min(50, candidate_limit - len(candidates)))
            unique_for_query = list(dict.fromkeys(channel_id for channel_id in found if channel_id))
            new_ids = [channel_id for channel_id in unique_for_query if channel_id not in seen]
            seen.update(new_ids)
            candidates.extend(new_ids)
            query_results.append({"query": query, "status": "success", "returned": len(unique_for_query), "new_channels": len(new_ids)})
        except Exception as exc:
            message = str(exc)
            log.warning("YouTube discovery query failed query=%r error=%s", query, message)
            query_results.append({"query": query, "status": "failed", "returned": 0, "new_channels": 0, "error": message})
            errors.append(f"{query}: {message}")
            if _is_quota_error(exc):
                quota_exhausted = True
                break

    if quota_exhausted:
        for query in niche_queries[len(query_results):query_limit]:
            query_results.append({"query": query, "status": "skipped_quota_exhausted", "returned": 0, "new_channels": 0})

    details: list[dict] = []
    if not quota_exhausted:
        for start in range(0, len(candidates), 50):
            try:
                details.extend(yt.channels(candidates[start:start + 50]))
            except Exception as exc:
                message = str(exc)
                errors.append(f"Channel details: {message}")
                log.warning("YouTube channel details failed; preserving prior batches: %s", message)
                if _is_quota_error(exc):
                    quota_exhausted = True
                break

    stored_ids: set[str] = set()
    channel_results: list[dict] = []
    qualified = 0
    enrichment_quota_error = False
    for item in details:
        channel_id = item.get("id")
        if not channel_id or channel_id in stored_ids:
            continue
        stored_ids.add(channel_id)
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        playlists = item.get("contentDetails", {}).get("relatedPlaylists", {})
        if quota_exhausted or enrichment_quota_error:
            videos = []
        else:
            try:
                videos = yt.recent_videos(channel_id, limit=10, uploads_playlist_id=playlists.get("uploads"))
            except Exception as exc:
                message = str(exc)
                errors.append(f"Recent videos {channel_id}: {message}")
                log.warning("Video enrichment failed channel_id=%s error=%s", channel_id, message)
                videos = []
                if _is_quota_error(exc):
                    enrichment_quota_error = True

        influencer = db.scalar(select(Influencer).where(Influencer.channel_id == channel_id))
        if influencer is None:
            influencer = Influencer(channel_id=channel_id, name=snippet.get("title", "Unknown"), profile_url=f"https://www.youtube.com/channel/{channel_id}")
            db.add(influencer)
        influencer.name = snippet.get("title", "Unknown")
        influencer.niche = niche
        influencer.profile_url = f"https://www.youtube.com/channel/{channel_id}"
        influencer.description = snippet.get("description", "")
        influencer.published_at = parse_date(snippet.get("publishedAt"))
        influencer.country = snippet.get("country")
        influencer.subscribers = int(stats["subscriberCount"]) if stats.get("subscriberCount") is not None else None
        influencer.video_count = int(stats["videoCount"]) if stats.get("videoCount") is not None else None
        influencer.view_count = int(stats["viewCount"]) if stats.get("viewCount") is not None else None
        influencer.data_flags = json.dumps({"subscriber_count_hidden": stats.get("hiddenSubscriberCount", False), "country_available": bool(snippet.get("country")), "video_metrics_sampled": len(videos)})
        influencer.collected_at = datetime.now(timezone.utc)
        db.flush()

        existing_video_ids = {video.video_id for video in influencer.videos}
        for video in videos:
            video_id = video.get("id")
            video_stats = video.get("statistics", {})
            video_snippet = video.get("snippet", {})
            if not video_id or video_id in existing_video_ids:
                continue
            existing_video_ids.add(video_id)
            views = int(video_stats["viewCount"]) if "viewCount" in video_stats else None
            likes = int(video_stats["likeCount"]) if "likeCount" in video_stats else None
            comments = int(video_stats["commentCount"]) if "commentCount" in video_stats else None
            db.add(RecentVideo(influencer_id=influencer.id, video_id=video_id, title=video_snippet.get("title", ""), published_at=parse_date(video_snippet.get("publishedAt")), views=views, likes=likes, comments=comments, metrics_available=views is not None and (likes is not None or comments is not None), url=f"https://www.youtube.com/watch?v={video_id}"))
        db.flush()
        # The relationship may have been loaded above to deduplicate videos.
        # Query after flush so filtering sees both existing and newly inserted rows.
        saved_videos = db.scalars(select(RecentVideo).where(RecentVideo.influencer_id == influencer.id)).all()
        save_decision(db, influencer, saved_videos)
        evidence_exists = db.scalar(select(SourceEvidence.id).where(SourceEvidence.influencer_id == influencer.id, SourceEvidence.field == "channel_profile"))
        if evidence_exists is None:
            db.add(SourceEvidence(influencer_id=influencer.id, field="channel_profile", value=influencer.name, source_url=influencer.profile_url))
        db.commit()  # Keep every completed channel if a later enrichment call fails.
        if influencer.filter_status == "PASS":
            qualified += 1
        channel_results.append({"id": influencer.id, "name": influencer.name, "channel_id": influencer.channel_id, "niche": influencer.niche, "subscribers": influencer.subscribers, "filter_status": influencer.filter_status, "profile_url": influencer.profile_url})
        if enrichment_quota_error:
            # Do not issue further calls after learning the daily quota is exhausted.
            break

    successful_queries = sum(query["status"] == "success" for query in query_results)
    return {
        "success": successful_queries > 0,
        "discovered": len(seen),
        "stored": len(stored_ids),
        "total_in_database": db.query(Influencer).count(),
        "target": target_count,
        "niche": niche,
        "qualified": qualified,
        "qualified_this_run": qualified,
        "total_qualified": db.scalar(select(func.count(Influencer.id)).where(Influencer.filter_status == "PASS")) or 0,
        "channels": channel_results,
        "queries": query_results,
        "errors": errors,
        "quota_exhausted": quota_exhausted or enrichment_quota_error,
        "api_key_configured": bool(yt.key),
        "api_usage": getattr(yt, "stats", {"request_count": None, "quota_units_estimated": None}),
    }

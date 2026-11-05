import json, re
from app.db.models import EnrichmentRecord

EMAIL_RE=re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch((value or "").strip()))

def enrich_influencer(db, influencer, http_client=None):
    # Contact discovery is limited to public information returned by YouTube's
    # API. The API does not expose email addresses hidden behind About buttons.
    match=EMAIL_RE.search(influencer.description or "")
    influencer.email=match.group(0) if match and valid_email(match.group(0)) else "Not Found"
    influencer.email_source=influencer.profile_url if match else None
    links=list(dict.fromkeys(link.rstrip(".,)") for link in re.findall(r"https?://[^\s<>\]]+",influencer.description or "")))
    influencer.website=links[0].rstrip(".,)") if links else None
    try:
        data_flags=json.loads(influencer.data_flags or "{}")
    except (TypeError, ValueError):
        data_flags={}
    if not isinstance(data_flags, dict):
        data_flags={}
    data_flags["public_contact_links"]=links
    influencer.data_flags=json.dumps(data_flags)
    titles=[v.title for v in influencer.videos[:10]]
    themes=[]
    for term in ("python","AI","machine learning","coding","software","developer tools","tutorial"):
        if any(term.lower() in t.lower() for t in titles):themes.append(term)
    influencer.themes=json.dumps(themes)
    influencer.content_style="Tutorial or educational metadata" if titles else None
    db.add(EnrichmentRecord(influencer_id=influencer.id,status="COMPLETE",details=json.dumps({"email_source":"YouTube channel description" if match else None,"public_contact_links":links,"videos_checked":len(titles),"demographics":"not provided by YouTube Data API"})))
    return influencer

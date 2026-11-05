import json
from datetime import datetime, timezone, timedelta
from app.core.config import get_settings

TECH_TERMS={"technology","tech","ai","artificial intelligence","python","programming","developer","software","machine learning","coding","engineering","tutorial","generative ai"}
NICHE_ALIASES={
    "fintech": {"fintech","financial technology","finance","financial services","digital banking","banking","payments","payment systems","neobank","investing","blockchain","crypto","cryptocurrency"},
    "healthtech": {"healthtech","health technology","digital health","medical technology","healthcare technology"},
    "edtech": {"edtech","education technology","online learning","e-learning"},
    "gaming": {"gaming","game development","video games","gameplay","esports"},
    "beauty": {"beauty","makeup","skincare","cosmetics","haircare"},
    "fitness": {"fitness","workout","exercise","strength training","gym"},
    "fashion": {"fashion","style","clothing","outfit","streetwear"},
    "travel": {"travel","trip","destination","tourism"},
    "food": {"food","cooking","recipe","recipes","baking"},
    "education": {"education","learning","teaching","lesson","lessons","course"},
    "health & wellness": {"health","wellness","nutrition","mental health","healthy living"},
    "personal finance": {"personal finance","budgeting","saving money","investing","money management"},
}

def classify_niche(text: str, niche: str = "Technology") -> bool:
    low=(text or "").lower()
    requested=" ".join((niche or "Technology").lower().split())
    terms=TECH_TERMS if requested in {"technology","tech","technology education"} else NICHE_ALIASES.get(requested,{requested})
    return any(term in low for term in terms)

def calculate_engagement(videos: list[dict]) -> float | None:
    views=0; interactions=0; usable=False
    for v in videos:
        if v.get("views") is None or v.get("views",0)<=0: continue
        if v.get("likes") is None and v.get("comments") is None: continue
        views += v["views"]; interactions += (v.get("likes") or 0) + (v.get("comments") or 0); usable=True
    return (interactions/views*100) if usable and views else None

def evaluate(inf, videos: list, settings=None) -> tuple[str,list[str],str,float|None]:
    s=settings or get_settings(); codes=[]; text=(inf.get("name","")+" "+inf.get("description","")+" "+" ".join(v.get("title","") for v in videos)).lower()
    subs=inf.get("subscribers"); engagement=calculate_engagement(videos)
    if subs is None: codes.append("METRICS_UNAVAILABLE")
    elif subs < s.min_subscribers: codes.append("BELOW_MIN_SUBSCRIBERS")
    elif subs > s.max_subscribers: codes.append("ABOVE_MAX_SUBSCRIBERS")
    niche=inf.get("niche","Technology")
    if not classify_niche(text,niche): codes.append("NICHE_MISMATCH")
    cutoff=datetime.now(timezone.utc)-timedelta(days=s.recent_days)
    def is_recent(v):
        published=v.get("published_at")
        if not published:return False
        if published.tzinfo is None:published=published.replace(tzinfo=timezone.utc)
        return published>=cutoff
    recent=[v for v in videos if is_recent(v)]
    if len(recent)<3: codes.append("INSUFFICIENT_RECENT_CONTENT")
    if engagement is None: codes.append("METRICS_UNAVAILABLE")
    elif engagement<s.engagement_threshold: codes.append("LOW_VIDEO_ENGAGEMENT")
    hard={"BELOW_MIN_SUBSCRIBERS","ABOVE_MAX_SUBSCRIBERS","NICHE_MISMATCH","INSUFFICIENT_RECENT_CONTENT","LOW_VIDEO_ENGAGEMENT"}
    status="FAIL" if any(c in hard for c in codes) else "NEEDS_REVIEW" if codes else "PASS"
    explain={"BELOW_MIN_SUBSCRIBERS":"Below subscriber minimum","ABOVE_MAX_SUBSCRIBERS":"Above subscriber maximum","NICHE_MISMATCH":f"Selected niche '{niche}' not evident in public text","INSUFFICIENT_RECENT_CONTENT":"Fewer than 3 recent public videos in configured window","LOW_VIDEO_ENGAGEMENT":"Video engagement per view below threshold","METRICS_UNAVAILABLE":"Required public metrics are unavailable"}
    return status,sorted(set(codes)),"; ".join(explain[c] for c in sorted(set(codes))) or "Meets configured criteria",engagement

def save_decision(db, influencer, videos):
    from app.db.models import FilteringDecision
    obj={"name":influencer.name,"description":influencer.description,"subscribers":influencer.subscribers,"niche":influencer.niche}
    vs=[{"title":v.title,"published_at":v.published_at,"views":v.views,"likes":v.likes,"comments":v.comments} for v in videos]
    status,codes,reasons,eng=evaluate(obj,vs); influencer.filter_status=status;influencer.reason_codes=json.dumps(codes);influencer.reason_text=reasons;influencer.engagement_rate=eng;influencer.engagement_status="AVAILABLE" if eng is not None else "UNKNOWN"
    db.add(FilteringDecision(influencer_id=influencer.id,status=status,reason_codes=json.dumps(codes),reasons=reasons))

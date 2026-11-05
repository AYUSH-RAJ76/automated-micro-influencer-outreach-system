from datetime import datetime,timezone,timedelta
from app.services.filtering import evaluate,calculate_engagement,classify_niche
from app.core.config import Settings

def fixture_videos(n=3,likes=20):
    return [{"title":"Python programming tutorial","published_at":datetime.now(timezone.utc)-timedelta(days=i),"views":1000,"likes":likes,"comments":5} for i in range(n)]

def test_subscriber_boundaries():
    s=Settings(min_subscribers=5000,max_subscribers=100000,engagement_threshold=1)
    for count in (5000,100000):assert evaluate({"name":"Python developer","description":"AI coding tutorials","subscribers":count},fixture_videos(),s)[0]=="PASS"
    assert "BELOW_MIN_SUBSCRIBERS" in evaluate({"name":"Python","description":"","subscribers":4999},fixture_videos(),s)[1]

def test_niche_and_recent_content():
    status,codes,_,_=evaluate({"name":"Gardening","description":"Flowers","subscribers":50000},[{**v,"title":"Flower arranging"} for v in fixture_videos()],Settings())
    assert status=="FAIL" and "NICHE_MISMATCH" in codes
    assert "INSUFFICIENT_RECENT_CONTENT" in evaluate({"name":"Python","description":"","subscribers":50000},fixture_videos(2),Settings())[1]

def test_engagement_uses_metrics_and_unknown():
    assert calculate_engagement([{"views":100,"likes":5,"comments":5}])==10
    assert calculate_engagement([{"views":100,"likes":None,"comments":None}]) is None
    assert calculate_engagement([{"views":0,"likes":5,"comments":4}]) is None

def test_naive_database_timestamps_are_treated_as_utc():
    videos=[{"title":"Python tutorial","published_at":datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=i),"views":1000,"likes":20,"comments":5} for i in range(3)]
    status,_,_,_=evaluate({"name":"Python developer","description":"AI programming tutorials","subscribers":50000},videos,Settings())
    assert status=="PASS"

def test_selected_fintech_niche_classification():
    assert classify_niche("Fintech tools for digital banking and payments", "fintech")
    assert not classify_niche("Python programming tutorials", "fintech")
    status,codes,reason,_=evaluate({"name":"Banking creator","description":"Digital banking and fintech education","subscribers":50000,"niche":"fintech"},fixture_videos(),Settings())
    assert "NICHE_MISMATCH" not in codes

def test_discovery_categories_match_category_specific_public_text():
    assert classify_niche("Makeup and skincare routines", "Beauty")
    assert classify_niche("Weekly gameplay and esports news", "Gaming")
    assert classify_niche("Budgeting tips and money management", "Personal Finance")
    assert not classify_niche("Python programming tutorials", "Beauty")

def test_missing_metrics_needs_review():
    unknown=[{"title":"Python developer tutorial","published_at":datetime.now(timezone.utc)-timedelta(days=i),"views":None,"likes":None,"comments":None} for i in range(3)]
    status,codes,_,_=evaluate({"name":"Python AI","description":"Developer tutorials","subscribers":50000},unknown,Settings())
    assert status=="NEEDS_REVIEW" and "METRICS_UNAVAILABLE" in codes

"""
News Digest Scraper + OpenRouter AI Processor
Chạy qua GitHub Actions hoặc thủ công
"""

import os
import json
import hashlib
import re
import feedparser
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, db

# ─── CONFIG ────────────────────────────────────────────────────────────────────

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
FIREBASE_DB_URL    = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free" # free model trên OpenRouter

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = datetime.now(VN_TZ).strftime("%Y-%m-%d")

RSS_SOURCES = [
    {"name": "VnExpress",        "lang": "vi", "cat": "vn",      "url": "https://vnexpress.net/rss/tin-moi-nhat.rss"},
    {"name": "VnExpress Kinh tế","lang": "vi", "cat": "economy", "url": "https://vnexpress.net/rss/kinh-doanh.rss"},
    {"name": "Tuổi Trẻ",         "lang": "vi", "cat": "vn",      "url": "https://tuoitre.vn/rss/tin-moi-nhat.rss"},
    {"name": "Tuổi Trẻ Kinh tế", "lang": "vi", "cat": "economy", "url": "https://tuoitre.vn/rss/kinh-doanh.rss"},
    {"name": "Thanh Niên",       "lang": "vi", "cat": "vn",      "url": "https://thanhnien.vn/rss/home.rss"},
    {"name": "Dân Trí",          "lang": "vi", "cat": "vn",      "url": "https://dantri.com.vn/rss/home.rss"},
    {"name": "CafeF",            "lang": "vi", "cat": "economy", "url": "https://cafef.vn/rss/thi-truong-chung-khoan.rss"},
    {"name": "Reuters World",    "lang": "en", "cat": "world",   "url": "https://feeds.reuters.com/reuters/topNews"},
    {"name": "Reuters Business", "lang": "en", "cat": "economy", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "BBC World",        "lang": "en", "cat": "world",   "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "BBC Business",     "lang": "en", "cat": "economy", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "CNBC Economy",     "lang": "en", "cat": "economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
]

MAX_ARTICLES_PER_SOURCE = 10
MAX_ARTICLES_FOR_AI     = 40


# ─── FIREBASE INIT ─────────────────────────────────────────────────────────────

def init_firebase():
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not sa_json:
        raise ValueError("Thiếu FIREBASE_SERVICE_ACCOUNT env var")
    sa_dict = json.loads(sa_json)
    if not firebase_admin._apps:
        cred = credentials.Certificate(sa_dict)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    return db.reference()


# ─── SCRAPE RSS ────────────────────────────────────────────────────────────────

def article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def scrape_all_sources():
    articles = []
    for source in RSS_SOURCES:
        print(f"  → Scraping {source['name']}...")
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
                title   = entry.get("title", "").strip()
                link    = entry.get("link", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                summary = re.sub(r"<[^>]+>", "", summary)[:500]
                if not title or not link:
                    continue
                articles.append({
                    "id":      article_id(link),
                    "title":   title,
                    "summary": summary,
                    "url":     link,
                    "source":  source["name"],
                    "lang":    source["lang"],
                    "cat":     source["cat"],
                    "pubDate": entry.get("published", ""),
                    "date":    TODAY,
                })
                count += 1
            print(f"     {count} bài")
        except Exception as e:
            print(f"     ❌ Lỗi: {e}")
    print(f"  Tổng: {len(articles)} bài")
    return articles


# ─── OPENROUTER AI ─────────────────────────────────────────────────────────────

def build_prompt(articles: list) -> str:
    articles_text = ""
    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        articles_text += f"\n[{i}] [{a['source']}] [{a['cat'].upper()}]\nTiêu đề: {a['title']}\nTóm tắt: {a['summary'][:200]}\n---"

    return f"""Bạn là biên tập viên tin tức cao cấp. Phân tích {len(articles[:MAX_ARTICLES_FOR_AI])} bài báo dưới đây và trả về JSON thuần túy (KHÔNG có markdown, KHÔNG có backtick, KHÔNG có text ngoài JSON):

{{
  "clusters": [
    {{
      "topic": "Tên chủ đề ngắn gọn",
      "summary": "Tóm tắt 2-3 câu về chủ đề này bằng tiếng Việt",
      "articles": [1, 3, 5],
      "importance": 8
    }}
  ],
  "trends": [
    {{
      "rank": 1,
      "topic": "Tên xu hướng",
      "reason": "Lý do 1 câu tại sao đây là xu hướng nổi bật",
      "category": "economy",
      "score": 95
    }}
  ],
  "digest": {{
    "headline": "Tiêu đề tổng kết ngày hôm nay (1 câu ấn tượng)",
    "overview": "Nhận định tổng quan về ngày hôm nay trong 3-5 câu tiếng Việt",
    "key_points": ["Điểm nổi bật 1", "Điểm nổi bật 2", "Điểm nổi bật 3", "Điểm nổi bật 4", "Điểm nổi bật 5"]
  }}
}}

Yêu cầu:
- clusters: nhóm các bài cùng chủ đề, importance từ 1-10, tạo 5-10 clusters
- trends: top 5 xu hướng nổi bật nhất, score từ 1-100, category chỉ dùng: economy, world, vn
- digest: tóm tắt tổng thể ngày hôm nay
- Tất cả text bằng tiếng Việt trừ tên riêng

Danh sách bài báo:
{articles_text}"""


def process_with_ai(articles: list) -> dict:
    print("  → Gọi OpenRouter AI...")
    prompt = build_prompt(articles)

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
                "HTTP-Referer":  "https://nbcuong123.github.io/tintuc/",
                "X-Title":       "Tin247 News Digest",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()

        # Clean backticks nếu có
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*",     "", text)
        text = re.sub(r"\s*```$",     "", text)

        result = json.loads(text)
        clusters = result.get("clusters", [])
        trends   = result.get("trends", [])
        print(f"  ✅ AI xử lý xong: {len(clusters)} clusters, {len(trends)} trends")
        return result

    except Exception as e:
        print(f"  ❌ AI lỗi: {e}")
        if 'resp' in dir() and hasattr(resp, 'text'):
            print(f"  Response: {resp.text[:500]}")
        return {
            "clusters": [],
            "trends":   [],
            "digest": {
                "headline":   "Tổng hợp tin ngày " + TODAY,
                "overview":   "Không thể tạo tóm tắt tự động.",
                "key_points": []
            }
        }


# ─── SAVE TO FIREBASE ──────────────────────────────────────────────────────────

def save_to_firebase(ref, articles: list, ai_result: dict):
    print("  → Lưu lên Firebase...")

    existing  = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    for a in articles:
        if a["id"] not in existing:
            ref.child(f"articles/{TODAY}/{a['id']}").set(a)
            new_count += 1
    print(f"     {new_count} bài mới")

    ref.child(f"clusters/{TODAY}").set(ai_result.get("clusters", []))
    ref.child(f"trends/{TODAY}").set(ai_result.get("trends", []))

    digest = ai_result.get("digest", {})
    digest["date"]          = TODAY
    digest["updatedAt"]     = datetime.now(VN_TZ).isoformat()
    digest["totalArticles"] = len(articles)
    ref.child(f"digest/{TODAY}").set(digest)

    ref.child("meta/lastUpdated").set(datetime.now(VN_TZ).isoformat())
    ref.child("meta/lastDate").set(TODAY)
    print("  ✅ Firebase xong")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"📰 News Digest - {TODAY}")
    print(f"{'='*50}\n")

    print("1️⃣  Init Firebase...")
    ref = init_firebase()

    print("\n2️⃣  Scraping RSS...")
    articles = scrape_all_sources()
    if not articles:
        print("❌ Không có bài nào, dừng.")
        return

    print("\n3️⃣  Xử lý AI...")
    ai_result = process_with_ai(articles)

    print("\n4️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters', []))} clusters, {len(ai_result.get('trends', []))} trends")


if __name__ == "__main__":
    main()

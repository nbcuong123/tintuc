"""
News Digest Scraper + Gemini AI Processor
Chạy qua GitHub Actions hoặc thủ công
"""

import os
import json
import hashlib
import feedparser
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, db

# ─── CONFIG ────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FIREBASE_DB_URL = "https://tonghoptinngay-default-rtdb.firebaseio.com"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = datetime.now(VN_TZ).strftime("%Y-%m-%d")

RSS_SOURCES = [
    # Việt Nam - Tổng hợp
    {"name": "VnExpress",       "lang": "vi", "cat": "vn",      "url": "https://vnexpress.net/rss/tin-moi-nhat.rss"},
    {"name": "VnExpress Kinh tế","lang": "vi", "cat": "economy", "url": "https://vnexpress.net/rss/kinh-doanh.rss"},
    {"name": "Tuổi Trẻ",        "lang": "vi", "cat": "vn",      "url": "https://tuoitre.vn/rss/tin-moi-nhat.rss"},
    {"name": "Tuổi Trẻ Kinh tế","lang": "vi", "cat": "economy", "url": "https://tuoitre.vn/rss/kinh-doanh.rss"},
    {"name": "Thanh Niên",      "lang": "vi", "cat": "vn",      "url": "https://thanhnien.vn/rss/home.rss"},
    {"name": "Dân Trí",         "lang": "vi", "cat": "vn",      "url": "https://dantri.com.vn/rss/home.rss"},
    {"name": "CafeF",           "lang": "vi", "cat": "economy", "url": "https://cafef.vn/rss/thi-truong-chung-khoan.rss"},
    # Quốc tế
    {"name": "Reuters World",   "lang": "en", "cat": "world",   "url": "https://feeds.reuters.com/reuters/topNews"},
    {"name": "Reuters Business","lang": "en", "cat": "economy", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "BBC World",       "lang": "en", "cat": "world",   "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "BBC Business",    "lang": "en", "cat": "economy", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "CNBC Economy",    "lang": "en", "cat": "economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
]

MAX_ARTICLES_PER_SOURCE = 10
MAX_ARTICLES_FOR_AI = 40  # giới hạn gửi cho Gemini mỗi lần


# ─── FIREBASE INIT ─────────────────────────────────────────────────────────────

def init_firebase():
    """Init Firebase từ service account JSON trong env var"""
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
    """Tạo ID ngắn từ URL"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def scrape_all_sources():
    """Scrape tất cả RSS sources, trả về list articles"""
    articles = []

    for source in RSS_SOURCES:
        print(f"  → Scraping {source['name']}...")
        try:
            feed = feedparser.parse(source["url"])
            count = 0
            for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                # Xóa HTML tags đơn giản
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

                if not title or not link:
                    continue

                pub_date = entry.get("published", "")
                articles.append({
                    "id":      article_id(link),
                    "title":   title,
                    "summary": summary,
                    "url":     link,
                    "source":  source["name"],
                    "lang":    source["lang"],
                    "cat":     source["cat"],
                    "pubDate": pub_date,
                    "date":    TODAY,
                })
                count += 1
            print(f"     {count} bài")
        except Exception as e:
            print(f"     ❌ Lỗi: {e}")

    print(f"  Tổng: {len(articles)} bài")
    return articles


# ─── GEMINI AI ─────────────────────────────────────────────────────────────────

def init_gemini():
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-2.0-flash")


def build_prompt(articles: list) -> str:
    """Tạo prompt gửi cho Gemini"""
    articles_text = ""
    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        articles_text += f"""
[{i}] [{a['source']}] [{a['cat'].upper()}]
Tiêu đề: {a['title']}
Tóm tắt: {a['summary'][:200]}
---"""

    return f"""Bạn là biên tập viên tin tức cao cấp. Phân tích {len(articles[:MAX_ARTICLES_FOR_AI])} bài báo dưới đây và trả về JSON (KHÔNG có markdown, KHÔNG có backtick):

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
      "category": "economy|world|vn",
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
- clusters: nhóm các bài cùng chủ đề, importance từ 1-10
- trends: top 5 xu hướng nổi bật nhất ngày hôm nay, score từ 1-100
- digest: bản tóm tắt tổng thể ngày hôm nay
- Tất cả text bằng tiếng Việt trừ tên riêng

Danh sách bài báo:
{articles_text}"""


def process_with_ai(model, articles: list) -> dict:
    """Gọi Gemini, parse kết quả"""
    print("  → Gọi Gemini AI...")
    prompt = build_prompt(articles)

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()

        # Clean nếu có backtick
        import re
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*",     "", text)
        text = re.sub(r"\s*```$",     "", text)

        result = json.loads(text)
        print("  ✅ AI xử lý xong")
        return result

    except Exception as e:
        print(f"  ❌ AI lỗi: {e}")
        return {
            "clusters": [],
            "trends": [],
            "digest": {
                "headline": "Tổng hợp tin ngày " + TODAY,
                "overview": "Không thể tạo tóm tắt tự động.",
                "key_points": []
            }
        }


# ─── SAVE TO FIREBASE ──────────────────────────────────────────────────────────

def save_to_firebase(ref, articles: list, ai_result: dict):
    """Lưu tất cả lên Firebase"""
    print("  → Lưu lên Firebase...")

    # 1. Lưu từng bài (chỉ lưu bài chưa có)
    existing = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    for a in articles:
        if a["id"] not in existing:
            ref.child(f"articles/{TODAY}/{a['id']}").set(a)
            new_count += 1

    print(f"     {new_count} bài mới")

    # 2. Lưu clusters
    ref.child(f"clusters/{TODAY}").set(ai_result.get("clusters", []))

    # 3. Lưu trends
    ref.child(f"trends/{TODAY}").set(ai_result.get("trends", []))

    # 4. Lưu daily digest
    digest = ai_result.get("digest", {})
    digest["date"] = TODAY
    digest["updatedAt"] = datetime.now(VN_TZ).isoformat()
    digest["totalArticles"] = len(articles)
    ref.child(f"digest/{TODAY}").set(digest)

    # 5. Cập nhật meta (ngày mới nhất)
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
    model = init_gemini()
    ai_result = process_with_ai(model, articles)

    print("\n4️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters', []))} clusters, {len(ai_result.get('trends', []))} trends")


if __name__ == "__main__":
    main()

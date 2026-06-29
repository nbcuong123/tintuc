"""
News Digest Scraper + OpenRouter AI Processor (với dịch EN→VI)
"""

import os, json, hashlib, re
import feedparser, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, db

# ─── CONFIG ───────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
FIREBASE_DB_URL    = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"
OPENROUTER_MODEL   = "openrouter/auto"

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


# ─── FIREBASE ─────────────────────────────────────────────────
def init_firebase():
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not sa_json:
        raise ValueError("Thiếu FIREBASE_SERVICE_ACCOUNT env var")
    sa_dict = json.loads(sa_json)
    if not firebase_admin._apps:
        cred = credentials.Certificate(sa_dict)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    return db.reference()


# ─── SCRAPE ───────────────────────────────────────────────────
def article_id(url):
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
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", entry.get("description", "")).strip())[:500]
                if not title or not link:
                    continue
                articles.append({
                    "id":       article_id(link),
                    "title":    title,
                    "title_vi": "",      # sẽ được điền sau khi AI dịch
                    "summary":  summary,
                    "summary_vi": "",    # sẽ được điền sau khi AI dịch
                    "url":      link,
                    "source":   source["name"],
                    "lang":     source["lang"],
                    "cat":      source["cat"],
                    "pubDate":  entry.get("published", ""),
                    "date":     TODAY,
                })
                count += 1
            print(f"     {count} bài")
        except Exception as e:
            print(f"     ❌ Lỗi: {e}")
    print(f"  Tổng: {len(articles)} bài")
    return articles


# ─── AI ───────────────────────────────────────────────────────
def build_prompt(articles):
    articles_text = ""
    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        lang_note = " [EN→VI]" if a.get("lang") == "en" else ""
        articles_text += f"\n[{i}]{lang_note} [{a['source']}] [{a['cat'].upper()}]\nTiêu đề: {a['title']}\nTóm tắt: {a['summary'][:200]}\n---"

    n = len(articles[:MAX_ARTICLES_FOR_AI])
    return f"""Bạn là biên tập viên tin tức cao cấp, thành thạo dịch Anh-Việt. Phân tích {n} bài báo và trả về JSON thuần túy (KHÔNG markdown, KHÔNG backtick, KHÔNG text ngoài JSON).

Bài đánh dấu [EN->VI] là bài tiếng Anh — dịch title_vi và summary_vi sang tiếng Việt tự nhiên.

{{
  "articles_vi": [
    {{
      "index": 1,
      "title_vi": "Tiêu đề tiếng Việt",
      "summary_vi": "Tóm tắt tiếng Việt 1-2 câu"
    }}
  ],
  "clusters": [
    {{
      "topic": "Tên chủ đề ngắn gọn tiếng Việt",
      "summary": "Tóm tắt 2-3 câu tiếng Việt",
      "articles": [1, 3, 5],
      "importance": 8
    }}
  ],
  "trends": [
    {{
      "rank": 1,
      "topic": "Tên xu hướng tiếng Việt",
      "reason": "Lý do 1 câu",
      "category": "economy",
      "score": 95
    }}
  ],
  "digest": {{
    "headline": "Tiêu đề tổng kết ngày (1 câu ấn tượng)",
    "overview": "Nhận định tổng quan 3-5 câu tiếng Việt",
    "key_points": ["Điểm 1", "Điểm 2", "Điểm 3", "Điểm 4", "Điểm 5"]
  }}
}}

Yêu cầu:
- articles_vi: dịch/giữ TẤT CẢ {n} bài, index khớp số thứ tự [N]
- clusters: 5-10 nhóm chủ đề, importance 1-10
- trends: top 5, score 1-100, category chỉ dùng: economy/world/vn
- Tất cả text tiếng Việt tự nhiên, tên riêng giữ nguyên

Danh sách bài báo:
{articles_text}"""


def process_with_ai(articles):
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
                "model":       OPENROUTER_MODEL,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens":  8000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*",     "", text)
        text = re.sub(r"\s*```$",     "", text)

        result   = json.loads(text)
        clusters = result.get("clusters", [])
        trends   = result.get("trends", [])
        arts_vi  = result.get("articles_vi", [])
        print(f"  ✅ AI xử lý xong: {len(clusters)} clusters, {len(trends)} trends, {len(arts_vi)} bài dịch")
        return result

    except Exception as e:
        print(f"  ❌ AI lỗi: {e}")
        if 'resp' in dir() and hasattr(resp, 'text'):
            print(f"  Response: {resp.text[:500]}")
        return {"clusters": [], "trends": [], "articles_vi": [], "digest": {
            "headline": "Tổng hợp tin ngày " + TODAY,
            "overview": "Không thể tạo tóm tắt tự động.",
            "key_points": []
        }}


# ─── MERGE TRANSLATIONS ────────────────────────────────────────
def merge_translations(articles, ai_result):
    """Gộp bản dịch AI vào từng article"""
    arts_vi = ai_result.get("articles_vi", [])
    lookup = {item["index"]: item for item in arts_vi if "index" in item}
    print(f"  → Merge: {len(lookup)} bản dịch cho {len(articles[:MAX_ARTICLES_FOR_AI])} bài")
    # Debug: in 3 mẫu đầu
    sample = arts_vi[:3] if arts_vi else []
    print(f"  DEBUG arts_vi sample: {sample}")
    print(f"  DEBUG lookup keys: {list(lookup.keys())[:5]}")

    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        vi = lookup.get(i, {})
        if a["lang"] == "en":
            t = vi.get("title_vi", "").strip()
            s = vi.get("summary_vi", "").strip()
            a["title_vi"]   = t if t else a["title"]
            a["summary_vi"] = s if s else a["summary"]
            if not t:
                title_short = a['title'][:50]
                print(f"     ⚠️  Thiếu dịch bài [{i}]: {title_short}")
        else:
            a["title_vi"]   = a["title"]
            a["summary_vi"] = a["summary"]
    return articles


# ─── SAVE TO FIREBASE ─────────────────────────────────────────
def save_to_firebase(ref, articles, ai_result):
    print("  → Lưu lên Firebase...")

    existing  = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    upd_count = 0
    for a in articles:
        if a["id"] not in existing:
            ref.child(f"articles/{TODAY}/{a['id']}").set(a)
            new_count += 1
        else:
            # Luôn update title_vi/summary_vi
            t_vi = a.get("title_vi", "")
            s_vi = a.get("summary_vi", "")
            if a.get("lang") == "en" and upd_count < 3:
                print(f"  DEBUG update [{a['id']}] lang=en title_vi={repr(t_vi[:40])}")
            ref.child(f"articles/{TODAY}/{a['id']}/title_vi").set(t_vi)
            ref.child(f"articles/{TODAY}/{a['id']}/summary_vi").set(s_vi)
            upd_count += 1
    print(f"     {new_count} bài mới, {upd_count} bài cập nhật dịch")

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


# ─── MAIN ─────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}\n📰 News Digest - {TODAY}\n{'='*50}\n")

    print("1️⃣  Init Firebase...")
    ref = init_firebase()

    print("\n2️⃣  Scraping RSS...")
    articles = scrape_all_sources()
    if not articles:
        print("❌ Không có bài nào, dừng.")
        return

    print("\n3️⃣  Xử lý AI + dịch EN→VI...")
    ai_result = process_with_ai(articles)

    print("\n4️⃣  Merge bản dịch...")
    articles = merge_translations(articles, ai_result)

    print("\n5️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters',[]))} clusters, {len(ai_result.get('trends',[]))} trends")

if __name__ == "__main__":
    main()

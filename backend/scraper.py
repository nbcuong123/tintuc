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
MAX_ARTICLES_FOR_AI     = 100


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
                    "title_vi": "",
                    "summary":  summary,
                    "summary_vi": "",
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


def balance_languages(articles):
    """Trộn đều bài VI và EN để đảm bảo AI có cả 2 ngôn ngữ"""
    vi_articles = [a for a in articles if a["lang"] == "vi"]
    en_articles = [a for a in articles if a["lang"] == "en"]
    
    print(f"  → Balance: {len(vi_articles)} VI, {len(en_articles)} EN")
    
    # Trộn xen kẽ: 2 VI, 1 EN, lặp lại
    balanced = []
    vi_idx, en_idx = 0, 0
    
    while vi_idx < len(vi_articles) or en_idx < len(en_articles):
        # Thêm 2 bài VI
        for _ in range(2):
            if vi_idx < len(vi_articles):
                balanced.append(vi_articles[vi_idx])
                vi_idx += 1
        # Thêm 1 bài EN
        if en_idx < len(en_articles):
            balanced.append(en_articles[en_idx])
            en_idx += 1
    
    print(f"  ✅ Đã trộn: {len(balanced)} bài (xen kẽ VI-EN)")
    return balanced


# ─── AI ───────────────────────────────────────────────────────
def build_prompt(articles):
    """Prompt rõ ràng, bắt buộc AI trả về ĐỦ tất cả bài"""
    subset = articles[:MAX_ARTICLES_FOR_AI]
    n = len(subset)
    en_count = sum(1 for a in subset if a.get("lang") == "en")

    articles_text = ""
    for i, a in enumerate(subset, 1):
        flag = "🇬🇧EN" if a.get("lang") == "en" else "🇻🇳VI"
        articles_text += f"\n[{i}] {flag} | {a['source']} | {a['cat'].upper()}\nTitle: {a['title']}\nSummary: {a['summary'][:250]}\n---"

    return f"""Bạn là biên tập viên tin tức. Trả về JSON thuần túy (KHÔNG markdown, KHÔNG backtick, KHÔNG text ngoài JSON).

TỔNG: {n} bài, trong đó {en_count} bài tiếng Anh (🇬🇧EN) cần dịch sang tiếng Việt.

NHIỆM VỤ articles_vi:
- PHẢI có đúng {n} phần tử, "index" chạy từ 1 đến {n}
- Bài 🇻🇳VI: copy nguyên title_vi = title, summary_vi = summary
- Bài 🇬🇧EN: dịch title_vi và summary_vi sang tiếng Việt TỰ NHIÊN, súc tích

JSON format:
{{
  "articles_vi": [
    {{"index": 1, "title_vi": "...", "summary_vi": "..."}},
    {{"index": 2, "title_vi": "...", "summary_vi": "..."}},
    ...đúng {n} phần tử...
  ],
  "clusters": [
    {{"topic": "Chủ đề", "summary": "Tóm tắt 2-3 câu", "articles": [1,3,5], "importance": 8}}
  ],
  "trends": [
    {{"rank": 1, "topic": "Xu hướng", "reason": "Lý do", "category": "economy", "score": 95}}
  ],
  "digest": {{
    "headline": "Tiêu đề tổng kết ngày",
    "overview": "Tổng quan 3-5 câu",
    "key_points": ["Điểm 1", "Điểm 2", "Điểm 3", "Điểm 4", "Điểm 5"]
  }}
}}

Yêu cầu khác:
- clusters: 5-10 nhóm, importance 1-10
- trends: top 5, score 1-100, category chỉ: economy/world/vn
- Tên riêng giữ nguyên

DANH SÁCH BÀI:
{articles_text}

⚠️ QUAN TRỌNG: articles_vi PHẢI có ĐÚNG {n} phần tử với index từ 1 đến {n}. Bắt đầu JSON ngay:"""


def parse_ai_response(text):
    """Parse JSON từ response AI, xử lý nhiều trường hợp lỗi"""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)
    text = text.strip()

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)

    return json.loads(text)


def process_with_ai(articles):
    print("  → Gọi OpenRouter AI...")
    prompt = build_prompt(articles)
    subset_count = len(articles[:MAX_ARTICLES_FOR_AI])

    resp = None
    text = ""
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
                "max_tokens":  12000,
            },
            timeout=240,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        print(f"  → AI raw response length: {len(text)} chars")

        result = parse_ai_response(text)

        arts_vi = result.get("articles_vi", [])
        if len(arts_vi) < subset_count:
            print(f"  ⚠️  AI trả về thiếu: {len(arts_vi)}/{subset_count} bài. Thử retry...")
            result = retry_ai(articles, prompt, result)

        clusters = result.get("clusters", [])
        trends   = result.get("trends", [])
        arts_vi  = result.get("articles_vi", [])
        print(f"  ✅ AI xong: {len(arts_vi)} bài dịch, {len(clusters)} clusters, {len(trends)} trends")
        return result

    except json.JSONDecodeError as e:
        print(f"  ❌ JSON parse lỗi: {e}")
        print(f"  Response text (đầu): {text[:500] if text else 'N/A'}")
        return fallback_result()
    except Exception as e:
        print(f"  ❌ AI lỗi: {e}")
        if resp is not None and hasattr(resp, 'text'):
            print(f"  Response: {resp.text[:500]}")
        return fallback_result()


def retry_ai(articles, original_prompt, partial_result):
    """Retry khi AI trả về thiếu articles_vi"""
    print("  → Retry với prompt bổ sung...")
    subset_count = len(articles[:MAX_ARTICLES_FOR_AI])
    existing_indices = [a.get("index") for a in partial_result.get("articles_vi", [])]
    missing = [i for i in range(1, subset_count + 1) if i not in existing_indices]

    missing_text = ""
    for i in missing:
        a = articles[i - 1] if i - 1 < len(articles) else None
        if a:
            flag = "🇬🇧EN" if a.get("lang") == "en" else "🇻🇳VI"
            missing_text += f"\n[{i}] {flag} | {a['source']}\nTitle: {a['title']}\nSummary: {a['summary'][:200]}\n---"

    retry_prompt = f"""Lần trước bạn trả về thiếu {len(missing)} bài. Bổ sung ĐỦ các bài còn thiếu sau:

{missing_text}

Trả về JSON chỉ có articles_vi với đúng {len(missing)} phần tử thiếu:
{{"articles_vi": [{{"index": {missing[0] if missing else 1}, "title_vi": "...", "summary_vi": "..."}}, ...]}}

Bắt đầu JSON ngay:"""

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
                "messages":    [{"role": "user", "content": retry_prompt}],
                "temperature": 0.2,
                "max_tokens":  6000,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        retry_data = parse_ai_response(text)

        existing = {a["index"]: a for a in partial_result.get("articles_vi", []) if "index" in a}
        for item in retry_data.get("articles_vi", []):
            idx = item.get("index")
            if idx is not None:
                existing[idx] = item

        partial_result["articles_vi"] = [existing[k] for k in sorted(existing.keys())]
        print(f"  ✅ Retry xong: giờ có {len(partial_result['articles_vi'])} bài")
        return partial_result
    except Exception as e:
        print(f"  ❌ Retry lỗi: {e}")
        return partial_result


def fallback_result():
    """Kết quả fallback khi AI lỗi hoàn toàn"""
    return {
        "clusters": [],
        "trends": [],
        "articles_vi": [],
        "digest": {
            "headline": "Tổng hợp tin ngày " + TODAY,
            "overview": "Không thể tạo tóm tắt tự động.",
            "key_points": []
        }
    }


# ─── MERGE TRANSLATIONS ────────────────────────────────────────
def merge_translations(articles, ai_result):
    """Gộp bản dịch AI vào từng article - đảm bảo 100% bài EN có title_vi"""
    arts_vi = ai_result.get("articles_vi", [])

    lookup = {}
    for item in arts_vi:
        idx = item.get("index")
        if idx is not None:
            lookup[int(idx)] = item

    en_total = 0
    en_translated = 0
    en_fallback = 0
    en_missing_details = []

    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        if a["lang"] == "en":
            en_total += 1
            vi = lookup.get(i, {})
            
            t_vi = (vi.get("title_vi") or "").strip()
            s_vi = (vi.get("summary_vi") or "").strip()

            has_valid_translation = (
                t_vi and 
                t_vi != a["title"] and
                len(t_vi) > 5
            )

            if has_valid_translation:
                a["title_vi"] = t_vi
                a["summary_vi"] = s_vi if s_vi else a["summary"]
                en_translated += 1
            else:
                a["title_vi"] = a["title"]
                a["summary_vi"] = a["summary"]
                en_fallback += 1
                en_missing_details.append({
                    "index": i,
                    "source": a["source"],
                    "title": a["title"][:50]
                })
        else:
            a["title_vi"] = a["title"]
            a["summary_vi"] = a["summary"]

    print(f"  📊 Thống kê dịch EN→VI:")
    print(f"     ✅ {en_translated}/{en_total} bài có bản dịch")
    if en_fallback > 0:
        print(f"     ⚠️  {en_fallback}/{en_total} bài dùng title gốc (AI không dịch)")
        for item in en_missing_details[:5]:
            print(f"        [{item['index']}] {item['source']}: {item['title']}")
        if len(en_missing_details) > 5:
            print(f"        ... và {len(en_missing_details) - 5} bài khác")

    return articles


# ─── SAVE TO FIREBASE ─────────────────────────────────────────
def save_to_firebase(ref, articles, ai_result):
    print("  → Lưu lên Firebase...")

    existing  = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    upd_count = 0
    new_with_vi = 0
    new_no_vi = 0
    
    # Lặp với index để lưu articleIndex
    for idx, a in enumerate(articles, 1):
        t_vi = a.get("title_vi", "")
        s_vi = a.get("summary_vi", "")
        
        # Thêm articleIndex vào article
        a["articleIndex"] = idx
        
        if a["id"] not in existing:
            ref.child(f"articles/{TODAY}/{a['id']}").set(a)
            new_count += 1
            if a["lang"] == "en":
                if t_vi and t_vi != a["title"]:
                    new_with_vi += 1
                else:
                    new_no_vi += 1
        else:
            ref.child(f"articles/{TODAY}/{a['id']}/title_vi").set(t_vi)
            ref.child(f"articles/{TODAY}/{a['id']}/summary_vi").set(s_vi)
            ref.child(f"articles/{TODAY}/{a['id']}/articleIndex").set(idx)
            upd_count += 1
    
    print(f"     📥 {new_count} bài mới (trong đó {new_with_vi} bài EN đã dịch, {new_no_vi} bài EN thiếu dịch)")
    print(f"     🔄 {upd_count} bài cũ đã cập nhật bản dịch")

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

    print("\n2.5️⃣ Balance VI/EN...")
    articles = balance_languages(articles)

    print("\n3️⃣  Xử lý AI + dịch EN→VI...")
    ai_result = process_with_ai(articles)

    print("\n4️⃣  Merge bản dịch...")
    articles = merge_translations(articles, ai_result)

    print("\n5️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters',[]))} clusters, {len(ai_result.get('trends',[]))} trends")

if __name__ == "__main__":
    main()

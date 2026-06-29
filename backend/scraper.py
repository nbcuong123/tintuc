"""
News Digest Scraper + OpenRouter AI Processor (với dịch EN→VI)
"""

import os, json, hashlib, re, time
import feedparser, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, db

# ─── CONFIG ───────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
FIREBASE_DB_URL    = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"

# ✅ Danh sách model ĐÚNG trên OpenRouter (cập nhật 2026)
OPENROUTER_MODELS = [
    "openai/gpt-4o-mini",                    # Chính: ổn định, rẻ
    "openrouter/auto",                        # Dự phòng 1: auto routing
    "meta-llama/llama-3.3-70b-instruct:free", # Dự phòng 2: free, mạnh
    "google/gemma-3-27b-it:free",             # Dự phòng 3: free
]

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
    """Trộn đều bài VI và EN"""
    vi_articles = [a for a in articles if a["lang"] == "vi"]
    en_articles = [a for a in articles if a["lang"] == "en"]
    
    print(f"  → Balance: {len(vi_articles)} VI, {len(en_articles)} EN")
    
    balanced = []
    vi_idx, en_idx = 0, 0
    
    while vi_idx < len(vi_articles) or en_idx < len(en_articles):
        for _ in range(2):
            if vi_idx < len(vi_articles):
                balanced.append(vi_articles[vi_idx])
                vi_idx += 1
        if en_idx < len(en_articles):
            balanced.append(en_articles[en_idx])
            en_idx += 1
    
    print(f"  ✅ Đã trộn: {len(balanced)} bài (xen kẽ VI-EN)")
    return balanced


# ─── AI ───────────────────────────────────────────────────────
def build_prompt(articles):
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
    "key_points": ["Điểm 1", "Điểm 2", "Điểm 3"]
  }}
}}

Yêu cầu:
- clusters: 5-8 nhóm, importance 1-10
- trends: top 5, score 1-100, category chỉ: economy/world/vn
- digest.key_points: chỉ 3 điểm (ngắn gọn)
- Tên riêng giữ nguyên

DANH SÁCH BÀI:
{articles_text}

QUAN TRỌNG: articles_vi PHẢI có ĐÚNG {n} phần tử. Bắt đầu JSON ngay:"""


def repair_json(text):
    """Sửa JSON bị cắt ngang do max_tokens"""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)
    text = text.strip()

    # Tìm JSON object lớn nhất
    match = re.search(r"\{[\s\S]*", text)
    if match:
        text = match.group(0)

    # Đếm và cân bằng ngoặc
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')
    
    # Thêm ngoặc thiếu
    text = text.rstrip().rstrip(',')
    while text.count('{') > text.count('}'):
        # Tìm vị trí cuối không phải ngoặc đóng
        text = text.rstrip().rstrip(',')
        text += ']}' if text.rstrip().endswith(']') or text.rstrip().endswith('"') else '}'
    while text.count('[') > text.count(']'):
        text = text.rstrip().rstrip(',') + ']}'
    
    # Thử parse, nếu fail thì cắt bớt
    try:
        return json.loads(text)
    except:
        pass
    
    # Cắt ở dấu } cuối cùng hợp lệ
    last_good = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == '}':
            try:
                return json.loads(text[:i+1])
            except:
                continue
    
    return None


def parse_ai_response(text):
    """Parse JSON với khả năng repair"""
    result = repair_json(text)
    if result:
        return result
    
    # Fallback: parse kiểu cũ
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)
    
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    
    return json.loads(text)


def process_with_ai(articles):
    print("  → Gọi OpenRouter AI...")
    prompt = build_prompt(articles)
    subset_count = len(articles[:MAX_ARTICLES_FOR_AI])

    for model_idx, model in enumerate(OPENROUTER_MODELS):
        print(f"  → Thử model [{model_idx+1}/{len(OPENROUTER_MODELS)}]: {model}")
        
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
                    "model":       model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens":  16000,  # ✅ TĂNG từ 12000 lên 16000
                },
                timeout=240,
            )
            
            if resp.status_code == 429:
                print(f"  ⚠️  Model {model} bị rate limit (429). Thử model khác...")
                time.sleep(2)
                continue
            
            if resp.status_code == 404:
                print(f"  ⚠️  Model {model} không tồn tại (404). Thử model khác...")
                continue
            
            resp.raise_for_status()
            
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                print(f"  ⚠️  Model {model} không có choices. Thử model khác...")
                continue
                
            choice = choices[0]
            
            # Kiểm tra finish_reason
            finish_reason = choice.get("finish_reason")
            if finish_reason == "error":
                err = choice.get("error", {})
                print(f"  ⚠️  Model {model} lỗi: {err.get('message', 'Unknown')}. Thử model khác...")
                continue
            
            text = choice["message"]["content"]
            print(f"  → AI response: {len(text)} chars, finish_reason={finish_reason}")
            
            # ✅ Log nếu bị truncate
            if finish_reason == "length":
                print(f"  ⚠️  Response bị cắt ngang (max_tokens). Đang repair JSON...")

            try:
                result = parse_ai_response(text)
            except json.JSONDecodeError as e:
                print(f"  ⚠️  Model {model} JSON parse lỗi: {e}")
                print(f"  Response (đầu): {text[:300]}")
                continue

            arts_vi = result.get("articles_vi", [])
            if len(arts_vi) < subset_count:
                print(f"  ⚠️  AI trả về thiếu: {len(arts_vi)}/{subset_count} bài. Retry...")
                result = retry_ai(articles, prompt, result)

            clusters = result.get("clusters", [])
            trends   = result.get("trends", [])
            arts_vi  = result.get("articles_vi", [])
            print(f"  ✅ AI xong ({model}): {len(arts_vi)} bài dịch, {len(clusters)} clusters, {len(trends)} trends")
            return result

        except requests.exceptions.HTTPError as e:
            print(f"  ⚠️  Model {model} HTTP lỗi: {e}")
            continue
        except Exception as e:
            print(f"  ⚠️  Model {model} lỗi: {e}")
            continue
    
    print(f"  ❌ Tất cả {len(OPENROUTER_MODELS)} models đều thất bại")
    return fallback_result()


def retry_ai(articles, original_prompt, partial_result):
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

    retry_prompt = f"""Lần trước bạn trả về thiếu {len(missing)} bài. Bổ sung ĐỦ các bài còn thiếu:

{missing_text}

Trả về JSON chỉ có articles_vi:
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
                "model":       OPENROUTER_MODELS[0],
                "messages":    [{"role": "user", "content": retry_prompt}],
                "temperature": 0.2,
                "max_tokens":  8000,
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
        print(f"  ✅ Retry xong: {len(partial_result['articles_vi'])} bài")
        return partial_result
    except Exception as e:
        print(f"  ❌ Retry lỗi: {e}")
        return partial_result


def fallback_result():
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
        print(f"     ⚠️  {en_fallback}/{en_total} bài dùng title gốc")

    return articles


# ─── GOOGLE TRENDS ────────────────────────────────────────────
def fetch_google_trends():
    """Lấy top trending từ Google Trends RSS"""
    print("  → Fetch Google Trends VN...")
    try:
        import xml.etree.ElementTree as ET
        url = "https://trends.google.com/trending/rss?geo=VN"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"ht": "https://trends.google.com/trending/rss"}
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            traffic = item.findtext("ht:approx_traffic", "", ns).strip()
            news_items = []
            for ni in item.findall("ht:news_item", ns)[:2]:
                news_title = ni.findtext("ht:news_item_title", "", ns).strip()
                news_url   = ni.findtext("ht:news_item_url", "", ns).strip()
                if news_title:
                    news_items.append({"title": news_title, "url": news_url})
            if title:
                items.append({
                    "rank":       len(items) + 1,
                    "keyword":    title,
                    "traffic":    traffic,
                    "news_items": news_items,
                })
            if len(items) >= 10:
                break
        print(f"     ✅ {len(items)} trending keywords")
        return items
    except Exception as e:
        print(f"     ❌ Lỗi Google Trends: {e}")
        return []


# ─── YOUTUBE TRENDING (Piped API + YouTube API fallback) ──────
def fetch_youtube_trending():
    """Lấy YouTube Trending VN qua Piped API hoặc YouTube Data API"""
    print("  → Fetch YouTube Trending VN...")
    
    # Thử Piped API trước (public, không cần key)
    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://api.piped.projectsegfau.lt",
    ]
    
    for instance in piped_instances:
        try:
            trending_url = f"{instance}/trending?region=VN"
            
            resp = requests.get(
                trending_url,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            
            if resp.status_code != 200:
                print(f"     ⚠️  Piped {instance}: HTTP {resp.status_code}")
                continue
            
            # Check content-type
            content_type = resp.headers.get('content-type', '')
            if 'application/json' not in content_type:
                print(f"     ⚠️  Piped {instance}: Không phải JSON")
                continue
            
            data = resp.json()
            if not isinstance(data, list) or not data:
                continue
            
            items = []
            for i, v in enumerate(data[:10], 1):
                video_id = v.get("url", "").replace("/watch?v=", "")
                if not video_id:
                    continue
                    
                items.append({
                    "rank":      i,
                    "videoId":   video_id,
                    "title":     v.get("title", ""),
                    "channel":   v.get("uploaderName", ""),
                    "thumbnail": v.get("thumbnail", f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"),
                    "viewCount": v.get("views", 0),
                    "url":       f"https://www.youtube.com/watch?v={video_id}",
                })
            
            if items:
                print(f"     ✅ {len(items)} trending videos (via Piped)")
                return items
                
        except Exception as e:
            print(f"     ⚠️  Piped {instance} lỗi: {e}")
            continue
    
    # Fallback: YouTube Data API (nếu có key)
    YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
    if YOUTUBE_API_KEY:
        print("  → Thử YouTube Data API...")
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part":       "snippet,statistics",
                    "chart":      "mostPopular",
                    "regionCode": "VN",
                    "maxResults": 10,
                    "key":        YOUTUBE_API_KEY,
                },
                timeout=15,
            )
            
            if resp.status_code == 200:
                data = resp.json()
                items = []
                for i, v in enumerate(data.get("items", []), 1):
                    sn = v.get("snippet", {})
                    stats = v.get("statistics", {})
                    items.append({
                        "rank":      i,
                        "videoId":   v.get("id", ""),
                        "title":     sn.get("title", ""),
                        "channel":   sn.get("channelTitle", ""),
                        "thumbnail": sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                        "viewCount": int(stats.get("viewCount", 0)),
                        "url":       f"https://www.youtube.com/watch?v={v.get('id','')}",
                    })
                
                if items:
                    print(f"     ✅ {len(items)} trending videos (via YouTube API)")
                    return items
            else:
                print(f"     ⚠️  YouTube API: HTTP {resp.status_code}")
        except Exception as e:
            print(f"     ⚠️  YouTube API lỗi: {e}")
    
    print(f"     ❌ Không lấy được YouTube trending")
    return []

# ─── SAVE TO FIREBASE ─────────────────────────────────────────
def save_to_firebase(ref, articles, ai_result, google_trends=None, youtube_trends=None):
    print("  → Lưu lên Firebase...")

    existing  = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    upd_count = 0
    new_with_vi = 0
    new_no_vi = 0
    
    for idx, a in enumerate(articles, 1):
        t_vi = a.get("title_vi", "")
        s_vi = a.get("summary_vi", "")
        
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
    
    print(f"     📥 {new_count} bài mới ({new_with_vi} EN đã dịch, {new_no_vi} EN thiếu)")
    print(f"     🔄 {upd_count} bài cũ cập nhật")

    ref.child(f"clusters/{TODAY}").set(ai_result.get("clusters", []))
    ref.child(f"trends/{TODAY}").set(ai_result.get("trends", []))

    digest = ai_result.get("digest", {})
    digest["date"]          = TODAY
    digest["updatedAt"]     = datetime.now(VN_TZ).isoformat()
    digest["totalArticles"] = len(articles)
    ref.child(f"digest/{TODAY}").set(digest)

    if google_trends:
        ref.child(f"google_trends/{TODAY}").set(google_trends)
    if youtube_trends:
        ref.child(f"youtube_trends/{TODAY}").set(youtube_trends)

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

    print("\n5️⃣  Fetch Google Trends + YouTube...")
    google_trends  = fetch_google_trends()
    youtube_trends = fetch_youtube_trending()

    print("\n6️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result, google_trends, youtube_trends)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters',[]))} clusters, {len(google_trends)} gtrends, {len(youtube_trends)} yt")

if __name__ == "__main__":
    main()

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

OPENROUTER_MODELS = [
    "openrouter/auto",
    "anthropic/claude-3-haiku",
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
# Giảm xuống 40 + dùng article_id thay vì index số để AI không "lẫn" khi gán cluster
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
    """
    Dùng article_id (string) thay vì số thứ tự để AI không bị lẫn lộn
    khi gán bài vào cluster — index số dễ "ảo giác" khi batch lớn,
    còn id duy nhất buộc AI phải copy chính xác từ danh sách đã cho.
    """
    subset = articles[:MAX_ARTICLES_FOR_AI]
    n = len(subset)
    en_count = sum(1 for a in subset if a.get("lang") == "en")

    articles_text = ""
    for a in subset:
        flag = "🇬🇧EN" if a.get("lang") == "en" else "🇻🇳VI"
        articles_text += f"\nID: {a['id']} | {flag} | {a['source']} | {a['cat'].upper()}\nTitle: {a['title']}\nSummary: {a['summary'][:200]}\n---"

    return f"""Bạn là biên tập viên tin tức cao cấp. Trả về JSON thuần túy (KHÔNG markdown, KHÔNG backtick).

TỔNG: {n} bài, trong đó {en_count} bài 🇬🇧EN CẦN DỊCH sang tiếng Việt.

⚠️ CỰC KỲ QUAN TRỌNG VỀ ID:
- Mỗi bài có 1 "ID" duy nhất (chuỗi 12 ký tự, ví dụ: a1b2c3d4e5f6)
- Khi gán bài vào "articles" của 1 cluster, PHẢI copy CHÍNH XÁC chuỗi ID đó, không được tự bịa ID mới, không được dùng ID của bài khác
- CHỈ dùng ID có trong danh sách dưới đây — TUYỆT ĐỐI không tạo ID không tồn tại
- 1 cluster chỉ chứa các bài THỰC SỰ cùng chủ đề — không nhét bài không liên quan vào chỉ để cluster có vẻ đầy đủ

JSON format:
{{
  "articles_vi": [
    {{"id": "a1b2c3d4e5f6", "title_vi": "...", "summary_vi": "Tóm tắt 2-3 câu nêu rõ: chuyện gì xảy ra, ai liên quan, vì sao đáng chú ý hoặc ảnh hưởng gì"}}
  ],
  "clusters": [
    {{"topic": "Chủ đề", "summary": "Tóm tắt", "articles": ["a1b2c3d4e5f6", "f6e5d4c3b2a1"], "importance": 8}}
  ],
  "trends": [
    {{"rank": 1, "topic": "Xu hướng", "reason": "Lý do", "category": "economy", "score": 95}}
  ],
  "digest": {{
    "headline": "Tiêu đề tổng kết ấn tượng cho cả ngày",
    "overview": "Đoạn tổng quan 4-6 câu mô tả bức tranh chung của ngày hôm nay: không khí chung, các mảng tin chính, điều gì đáng chú ý nhất",
    "key_points": ["Điểm nổi bật 1 (1 câu cụ thể)", "Điểm nổi bật 2", "Điểm nổi bật 3", "Điểm nổi bật 4", "Điểm nổi bật 5", "Điểm nổi bật 6"],
    "topic_groups": [
      {{"group_name": "Kinh tế - Tài chính", "summary": "Đoạn 2-4 câu khái quát diễn biến kinh tế nổi bật, nêu xu hướng chung và 1-2 sự kiện cụ thể quan trọng nhất"}},
      {{"group_name": "Xã hội - Đời sống", "summary": "Đoạn 2-4 câu về các vấn đề xã hội, đời sống nổi bật"}},
      {{"group_name": "Quốc tế", "summary": "Đoạn 2-4 câu về tình hình thế giới đáng chú ý"}}
    ]
  }}
}}

Yêu cầu BẮT BUỘC:
1. articles_vi: ĐÚNG {n} phần tử, mỗi phần tử có "id" khớp với 1 bài trong danh sách
2. Bài 🇻🇳VI: title_vi = title gốc, summary_vi PHẢI viết lại thành 2-3 câu chi tiết, diễn giải rõ ràng hơn dựa trên title+summary gốc đã cho (không copy nguyên văn)
3. Bài 🇬🇧EN: DỊCH title_vi và summary_vi sang tiếng Việt, summary_vi cũng phải 2-3 câu chi tiết
4. clusters: 5-8 nhóm, mỗi nhóm chỉ chứa ID của bài THỰC SỰ liên quan đến topic đó
5. trends: top 5
6. digest.key_points: 5-6 điểm cụ thể, mỗi điểm nêu rõ sự kiện thay vì chung chung
7. digest.topic_groups: PHẢI có 3-5 nhóm (chỉ chọn nhóm thực sự có tin trong danh sách, ví dụ: Kinh tế - Tài chính, Xã hội - Đời sống, Quốc tế, Công nghệ, Thể thao...), mỗi nhóm có summary riêng dựa trên các bài thuộc nhóm đó

DANH SÁCH BÀI ({n} bài, {en_count} EN cần dịch):
{articles_text}

Bắt đầu JSON ngay:"""


def repair_json(text):
    """Sửa JSON bị cắt ngang"""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)
    text = text.strip()

    match = re.search(r"\{[\s\S]*", text)
    if match:
        text = match.group(0)

    try:
        return json.loads(text)
    except:
        pass

    for i in range(len(text) - 1, max(0, len(text) - 5000), -1):
        if text[i] in ['}', ']']:
            try:
                candidate = text[:i+1]
                open_b = candidate.count('{')
                close_b = candidate.count('}')
                while open_b > close_b:
                    candidate += '}'
                    close_b += 1

                open_sq = candidate.count('[')
                close_sq = candidate.count(']')
                while open_sq > close_sq:
                    candidate += ']'
                    close_sq += 1

                return json.loads(candidate)
            except:
                continue

    return None


def parse_ai_response(text):
    """Parse JSON với repair"""
    result = repair_json(text)
    if result:
        return result

    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)

    return json.loads(text)


def validate_and_clean_clusters(result, valid_ids):
    """
    Lọc bỏ ID không tồn tại trong danh sách bài đã gửi cho AI.
    Đây là lưới an toàn cuối — kể cả khi AI vẫn bịa ID, frontend
    sẽ không bao giờ thấy bài lạc chủ đề trong cluster nữa.
    """
    clusters = result.get("clusters", [])
    cleaned = []
    dropped_total = 0

    for c in clusters:
        ids = c.get("articles", [])
        valid = [i for i in ids if i in valid_ids]
        dropped = len(ids) - len(valid)
        if dropped > 0:
            dropped_total += dropped
            print(f"     ⚠️  Cluster '{c.get('topic','?')}': loại {dropped} ID không hợp lệ")
        if valid:  # chỉ giữ cluster còn ít nhất 1 bài hợp lệ
            c["articles"] = valid
            cleaned.append(c)

    if dropped_total:
        print(f"  🧹 Tổng cộng đã loại {dropped_total} ID ảo giác khỏi clusters")

    result["clusters"] = cleaned
    return result


def process_with_ai(articles):
    print("  → Gọi OpenRouter AI...")
    prompt = build_prompt(articles)
    subset = articles[:MAX_ARTICLES_FOR_AI]
    subset_count = len(subset)
    valid_ids = {a["id"] for a in subset}

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
                    "temperature": 0.2,
                    "max_tokens":  16000,
                },
                timeout=240,
            )

            if resp.status_code == 429:
                print(f"  ⚠️  Model {model} rate limit (429). Thử model khác...")
                time.sleep(3)
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
            finish_reason = choice.get("finish_reason")

            if finish_reason == "error":
                err = choice.get("error", {})
                print(f"  ⚠️  Model {model} lỗi: {err.get('message', 'Unknown')}. Thử model khác...")
                continue

            text = choice["message"]["content"]
            print(f"  → AI response: {len(text)} chars, finish={finish_reason}")

            if finish_reason == "length":
                print(f"  ⚠️  Response bị cắt ngang. Đang repair...")

            try:
                result = parse_ai_response(text)
            except json.JSONDecodeError as e:
                print(f"  ⚠️  Model {model} JSON parse lỗi: {e}")
                print(f"  Response (đầu): {text[:300]}")
                continue

            arts_vi = result.get("articles_vi", [])
            # Kiểm tra cả số lượng LẪN chất lượng dịch — AI có thể trả đủ
            # số phần tử nhưng để title_vi rỗng/trùng title gốc cho các bài
            # cuối batch khi xử lý nhiều bài cùng lúc.
            en_ids_in_subset = {a["id"] for a in subset if a.get("lang") == "en"}
            vi_lookup = {item.get("id"): item for item in arts_vi if "id" in item}
            poorly_translated = 0
            for eid in en_ids_in_subset:
                item = vi_lookup.get(eid)
                t_vi = (item.get("title_vi") or "").strip() if item else ""
                orig_title = next((a["title"] for a in subset if a["id"] == eid), "")
                if not t_vi or t_vi == orig_title or len(t_vi) <= 5:
                    poorly_translated += 1

            if len(arts_vi) < subset_count or poorly_translated > 0:
                print(f"  ⚠️  AI thiếu: {len(arts_vi)}/{subset_count} bài, {poorly_translated} bài EN dịch kém. Retry...")
                result = retry_ai(subset, prompt, result, valid_ids)

            # Lưới an toàn: loại bỏ mọi ID không có thật trước khi lưu
            result = validate_and_clean_clusters(result, valid_ids)

            clusters = result.get("clusters", [])
            trends   = result.get("trends", [])
            arts_vi  = result.get("articles_vi", [])
            print(f"  ✅ AI xong ({model}): {len(arts_vi)} bài, {len(clusters)} clusters, {len(trends)} trends")
            return result

        except requests.exceptions.HTTPError as e:
            print(f"  ⚠️  Model {model} HTTP lỗi: {e}")
            continue
        except Exception as e:
            print(f"  ⚠️  Model {model} lỗi: {e}")
            continue

    print(f"  ❌ Tất cả {len(OPENROUTER_MODELS)} models đều thất bại")
    return fallback_result()


def retry_ai(subset, original_prompt, partial_result, valid_ids):
    print("  → Retry bổ sung...")
    arts_vi = partial_result.get("articles_vi", [])
    vi_lookup = {item.get("id"): item for item in arts_vi if "id" in item}

    # Lấy cả bài THIẾU lẫn bài có mặt nhưng DỊCH KÉM (title_vi rỗng/trùng gốc)
    missing = []
    for a in subset:
        if a.get("lang") != "en":
            continue  # chỉ cần retry bài tiếng Anh, bài VI không cần dịch
        item = vi_lookup.get(a["id"])
        t_vi = (item.get("title_vi") or "").strip() if item else ""
        if not t_vi or t_vi == a["title"] or len(t_vi) <= 5:
            missing.append(a)

    if not missing:
        return partial_result

    missing_text = ""
    for a in missing:
        flag = "🇬🇧EN" if a.get("lang") == "en" else "🇻🇳VI"
        missing_text += f"\nID: {a['id']} | {flag} | {a['source']}\nTitle: {a['title']}\nSummary: {a['summary'][:150]}\n---"

    retry_prompt = f"""Bổ sung {len(missing)} bài còn thiếu. Dùng đúng ID đã cho, không bịa ID mới:

{missing_text}

JSON: {{"articles_vi": [{{"id": "...", "title_vi": "...", "summary_vi": "..."}}]}}

Bắt đầu JSON:"""

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
                "temperature": 0.1,
                "max_tokens":  8000,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        retry_data = parse_ai_response(text)

        existing = {a["id"]: a for a in partial_result.get("articles_vi", []) if "id" in a}
        for item in retry_data.get("articles_vi", []):
            iid = item.get("id")
            if iid in valid_ids:
                existing[iid] = item

        partial_result["articles_vi"] = list(existing.values())
        print(f"  ✅ Retry: {len(partial_result['articles_vi'])} bài")
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
    """Gộp bản dịch AI - match theo article id (string), không dùng số thứ tự"""
    arts_vi = ai_result.get("articles_vi", [])

    lookup = {item["id"]: item for item in arts_vi if "id" in item}

    print(f"  🔍 DEBUG: AI trả về {len(lookup)} bản dịch")

    en_total = 0
    en_translated = 0
    en_fallback = 0
    en_missing_details = []

    for a in articles:
        if a["lang"] == "en":
            en_total += 1
            vi = lookup.get(a["id"], {})

            t_vi = (vi.get("title_vi") or "").strip()
            s_vi = (vi.get("summary_vi") or "").strip()

            if en_total <= 5:
                print(f"  🔍 EN bài [{a['id']}]: {a['source']}")
                print(f"     title: {a['title'][:50]}")
                print(f"     title_vi: {t_vi[:50] if t_vi else '(RỖNG)'}")

            if t_vi and t_vi != a["title"] and len(t_vi) > 5:
                a["title_vi"] = t_vi
                a["summary_vi"] = s_vi if s_vi else a["summary"]
                en_translated += 1
            else:
                a["title_vi"] = a["title"]
                a["summary_vi"] = a["summary"]
                en_fallback += 1
                en_missing_details.append({"id": a["id"], "source": a["source"], "title": a["title"][:50]})
        else:
            a["title_vi"] = a["title"]
            vi = lookup.get(a["id"], {})
            s_vi = (vi.get("summary_vi") or "").strip()
            a["summary_vi"] = s_vi if s_vi else a["summary"]

    print(f"  📊 Dịch EN→VI: {en_translated}/{en_total} bài (fallback: {en_fallback})")
    if en_fallback > 0:
        print(f"  ⚠️  {en_fallback} bài EN thiếu:")
        for item in en_missing_details[:5]:
            print(f"     [{item['id']}] {item['source']}: {item['title']}")

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
        print(f"     ✅ {len(items)} keywords")
        return items
    except Exception as e:
        print(f"     ❌ Lỗi: {e}")
        return []


# ─── YOUTUBE TRENDING ─────────────────────────────────────────
def fetch_youtube_trending():
    """Lấy YouTube Trending VN qua YouTube Data API"""
    print("  → Fetch YouTube Trending VN...")

    YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
    if not YOUTUBE_API_KEY:
        print("     ⚠️  Không có YOUTUBE_API_KEY. Bỏ qua.")
        return []

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
                print(f"     ✅ {len(items)} videos")
                return items
        else:
            print(f"     ⚠️  YouTube API: HTTP {resp.status_code}")
    except Exception as e:
        print(f"     ⚠️  YouTube API lỗi: {e}")

    return []


# ─── SAVE TO FIREBASE ─────────────────────────────────────────
def save_to_firebase(ref, articles, ai_result, google_trends=None, youtube_trends=None):
    print("  → Lưu Firebase...")

    existing  = ref.child(f"articles/{TODAY}").get() or {}
    new_count = 0
    upd_count = 0
    new_with_vi = 0
    new_no_vi = 0

    for a in articles:
        t_vi = a.get("title_vi", "")
        s_vi = a.get("summary_vi", "")

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
            upd_count += 1

    print(f"     📥 {new_count} mới ({new_with_vi} EN dịch, {new_no_vi} EN thiếu)")
    print(f"     🔄 {upd_count} cũ cập nhật")

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
        print("❌ Không có bài nào.")
        return

    print("\n2.5️⃣ Balance VI/EN...")
    articles = balance_languages(articles)

    print("\n3️⃣  AI + dịch EN→VI...")
    ai_result = process_with_ai(articles)

    print("\n4️⃣  Merge bản dịch...")
    articles = merge_translations(articles, ai_result)

    print("\n5️⃣  Fetch Google + YouTube...")
    google_trends  = fetch_google_trends()
    youtube_trends = fetch_youtube_trending()

    print("\n6️⃣  Lưu Firebase...")
    save_to_firebase(ref, articles, ai_result, google_trends, youtube_trends)

    print(f"\n✅ Hoàn tất! {len(articles)} bài, {len(ai_result.get('clusters',[]))} clusters")

if __name__ == "__main__":
    main()

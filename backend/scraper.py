"""
News Digest Scraper + Multi-Provider AI Processor (với dịch EN→VI)
Providers: Gemini (ưu tiên) → Groq → OpenRouter
"""

import os, json, hashlib, re, time, socket, sys, signal, contextlib
import feedparser, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, db

socket.setdefaulttimeout(15)
sys.stdout.reconfigure(line_buffering=True)


class WatchdogTimeout(Exception):
    pass


@contextlib.contextmanager
def hard_timeout(seconds):
    def _handler(signum, frame):
        raise WatchdogTimeout(f"Vượt quá {seconds}s (watchdog ngắt cứng)")
    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

# ─── CONFIG ───────────────────────────────────────────────────
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

FIREBASE_DB_URL = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"

# 🔑 Thứ tự ưu tiên: Gemini → Groq → OpenRouter
AI_PROVIDERS = []
if GEMINI_API_KEY:
    AI_PROVIDERS.append({
        "name":   "gemini",
        "models": [
            "gemini-2.5-flash-lite",     # miễn phí, cực nhanh
            "gemini-2.5-flash",          # thông minh hơn
            "gemini-2.0-flash",          # ổn định
        ],
    })
if GROQ_API_KEY:
    AI_PROVIDERS.append({
        "name":   "groq",
        "models": [
            "llama-3.3-70b-versatile",   # bỏ llama-3.1-8b-instant (luôn 413)
        ],
    })
if OPENROUTER_API_KEY:
    AI_PROVIDERS.append({
        "name":   "openrouter",
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
    })

if not AI_PROVIDERS:
    raise ValueError(
        "❌ Chưa cấu hình AI provider nào!\n"
        "Hãy set ít nhất 1 biến môi trường:\n"
        "  • GEMINI_API_KEY    (khuyến nghị - https://aistudio.google.com/apikey)\n"
        "  • GROQ_API_KEY      (https://console.groq.com/keys)\n"
        "  • OPENROUTER_API_KEY"
    )

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
    {"name": "Man Utd Official", "lang": "en", "cat": "mu",      "url": "https://www.manutd.com/Feeds/NewsSecondRSSFeed"},
    {"name": "Football365 MU",   "lang": "en", "cat": "mu",      "url": "https://www.football365.com/manchester-united/rss2"},
]

MAX_ARTICLES_PER_SOURCE = 10
# Tăng từ 6 → 8 bài/batch để giảm số batch (19 → 14), tránh Gemini 429
MAX_ARTICLES_FOR_AI     = 8


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


# ─── AI PROVIDERS ─────────────────────────────────────────────
def build_prompt(articles):
    """Prompt tối ưu để tránh hallucination và 413."""
    subset = articles[:MAX_ARTICLES_FOR_AI]
    n = len(subset)
    en_count = sum(1 for a in subset if a.get("lang") == "en")

    articles_text = ""
    for a in subset:
        flag = "EN" if a.get("lang") == "en" else "VI"
        articles_text += f"\n[{a['id']}|{flag}|{a['cat']}] {a['title']}\n{a['summary'][:80]}\n---"

    return f"""Biên tập viên tin tức. Trả JSON thuần (không markdown).
{n} bài ({en_count} EN cần dịch VI). ID là chuỗi 12 ký tự.

⚠️ CỰC KỲ QUAN TRỌNG: Mỗi summary_vi PHẢI tương ứng với đúng bài có cùng ID. KHÔNG được gán summary của bài này sang bài khác.

JSON:
{{
  "articles_vi": [{{"id": "...", "title_vi": "...", "summary_vi": "2-3 câu chi tiết"}}],
  "clusters": [{{"topic": "...", "summary": "...", "articles": ["id"], "importance": 8}}],
  "trends": [{{"rank": 1, "topic": "...", "reason": "...", "category": "economy", "score": 95}}],
  "digest": {{
    "headline": "...",
    "overview": "4-6 câu tổng quan",
    "key_points": ["điểm 1","điểm 2","điểm 3","điểm 4","điểm 5"],
    "topic_groups": [
      {{"group_name": "Kinh tế - Tài chính", "summary": "2-4 câu"}},
      {{"group_name": "Xã hội - Đời sống", "summary": "2-4 câu"}},
      {{"group_name": "Quốc tế", "summary": "2-4 câu"}}
    ]
  }}
}}

Yêu cầu:
1. articles_vi: ĐỦ {n} phần tử, id khớp danh sách
2. VI: title_vi=title gốc, summary_vi viết lại 2-3 câu
3. EN: DỊCH cả title_vi và summary_vi sang tiếng Việt
4. clusters: 3-5 nhóm, chỉ chứa ID liên quan thực sự
5. trends: top 3

DANH SÁCH ({n} bài):
{articles_text}

JSON:"""


def repair_json(text):
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
        if valid:
            c["articles"] = valid
            cleaned.append(c)
    if dropped_total:
        print(f"  🧹 Tổng cộng đã loại {dropped_total} ID ảo giác khỏi clusters")
    result["clusters"] = cleaned
    return result


# ─── GỌI TỪNG PROVIDER ────────────────────────────────────────
def call_groq(model, prompt):
    """Gọi Groq API"""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens":  8000,
        },
        timeout=(10, 120),
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise ValueError("Groq trả về không có choices")
    return choices[0]["message"]["content"], choices[0].get("finish_reason", "")


def call_gemini(model, prompt):
    """Gọi Google Gemini API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8000,
                "responseMimeType": "application/json",
            },
        },
        timeout=(10, 120),
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini trả về không có candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise ValueError("Gemini trả về không có parts")
    text = parts[0].get("text", "")
    finish = candidates[0].get("finishReason", "")
    return text, finish


def call_openrouter(model, prompt):
    """Gọi OpenRouter API"""
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
            "max_tokens":  8000,
        },
        timeout=(10, 120),
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise ValueError("OpenRouter trả về không có choices")
    return choices[0]["message"]["content"], choices[0].get("finish_reason", "")


PROVIDER_CALLERS = {
    "groq":       call_groq,
    "gemini":     call_gemini,
    "openrouter": call_openrouter,
}


def _process_sub_batch(pname, model, caller, sub_articles):
    """Xử lý 1 sub-batch nhỏ (khi chia nhỏ do 413)."""
    try:
        prompt = build_prompt(sub_articles)
        with hard_timeout(130):
            text, finish_reason = caller(model, prompt)
        print(f"     → Sub-batch {len(sub_articles)} bài: {len(text)} chars")
        result = parse_ai_response(text)
        return result
    except Exception as e:
        print(f"     ❌ Sub-batch lỗi: {e}")
        return None


def process_batch_with_ai(batch_articles, batch_label=""):
    """Xử lý 1 batch với retry logic cho 429."""
    print(f"  → Gọi AI{batch_label}...")
    subset = batch_articles[:MAX_ARTICLES_FOR_AI]
    valid_ids = {a["id"] for a in subset}

    prompt = build_prompt(subset)
    subset_count = len(subset)

    for provider in AI_PROVIDERS:
        pname = provider["name"]
        caller = PROVIDER_CALLERS[pname]

        for model_idx, model in enumerate(provider["models"]):
            # Retry logic cho 429: tối đa 2 lần retry
            for retry_attempt in range(3):
                print(f"  → Thử [{pname}] {model} ({model_idx+1}/{len(provider['models'])})")
                text = ""
                finish_reason = ""
                try:
                    with hard_timeout(130):
                        text, finish_reason = caller(model, prompt)
                    print(f"  → AI response: {len(text)} chars, finish={finish_reason}")

                    if finish_reason == "length":
                        print(f"  ⚠️  Response bị cắt ngang. Đang repair...")

                    try:
                        result = parse_ai_response(text)
                    except json.JSONDecodeError as e:
                        print(f"  ⚠️  [{pname}] {model} JSON parse lỗi: {e}")
                        break  # JSON lỗi → thử model khác

                    # Kiểm tra chất lượng
                    arts_vi = result.get("articles_vi", [])
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
                        result = retry_ai(pname, model, subset, prompt, result, valid_ids)

                    result = validate_and_clean_clusters(result, valid_ids)
                    clusters = result.get("clusters", [])
                    trends   = result.get("trends", [])
                    arts_vi  = result.get("articles_vi", [])
                    print(f"  ✅ AI xong ({pname}/{model}): {len(arts_vi)} bài, {len(clusters)} clusters, {len(trends)} trends")
                    return result

                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else "?"
                    err_text = str(e)[:120]

                    # 429 rate limit: retry cùng model với exponential backoff
                    if status == 429:
                        if retry_attempt < 2:
                            wait_time = 15 * (retry_attempt + 1)  # 15s, 30s
                            print(f"  ⚠️  [{pname}] {model} rate limit (429). Chờ {wait_time}s trước khi retry ({retry_attempt+1}/2)...")
                            time.sleep(wait_time)
                            continue  # retry cùng model
                        else:
                            print(f"  ⚠️  [{pname}] {model} đã retry 2 lần vẫn 429. Chuyển model khác...")
                            break  # thoát vòng retry, thử model khác

                    # 413 Payload Too Large: chia batch
                    if status == 413 and len(subset) > 4:
                        chunk_size = max(1, len(subset) // 4)
                        print(f"  ⚠️  [{pname}] {model} 413 Payload Too Large. Chia batch {len(subset)} → chunks {chunk_size}")
                        chunks = [subset[i:i + chunk_size] for i in range(0, len(subset), chunk_size)]
                        merged = {
                            "articles_vi": [],
                            "clusters":    [],
                            "trends":      [],
                            "digest":      {},
                        }
                        ok_chunks = 0
                        for idx, chunk in enumerate(chunks):
                            r = _process_sub_batch(pname, model, caller, chunk)
                            if r:
                                merged["articles_vi"].extend(r.get("articles_vi", []))
                                merged["clusters"].extend(r.get("clusters", []))
                                merged["trends"].extend(r.get("trends", []))
                                if not merged["digest"]:
                                    merged["digest"] = r.get("digest", {})
                                ok_chunks += 1
                        if ok_chunks > 0:
                            merged = validate_and_clean_clusters(
                                merged,
                                {a["id"] for a in subset}
                            )
                            print(f"  ✅ Gộp {ok_chunks}/{len(chunks)} chunks: {len(merged['articles_vi'])} bài")
                            return merged

                    # Các lỗi khác
                    print(f"  ⚠️  [{pname}] {model} HTTP {status}: {err_text}")
                    break  # thoát vòng retry, thử model khác

                except Exception as e:
                    print(f"  ⚠️  [{pname}] {model} lỗi: {str(e)[:150]}")
                    break  # lỗi khác → thử model khác

    print(f"  ❌ Tất cả providers/models đều thất bại")
    return fallback_result()


def retry_ai(provider_name, model, subset, original_prompt, partial_result, valid_ids):
    """Retry bổ sung các bài còn thiếu/dịch kém"""
    print("  → Retry bổ sung...")
    arts_vi = partial_result.get("articles_vi", [])
    vi_lookup = {item.get("id"): item for item in arts_vi if "id" in item}

    missing = []
    for a in subset:
        if a.get("lang") != "en":
            continue
        item = vi_lookup.get(a["id"])
        t_vi = (item.get("title_vi") or "").strip() if item else ""
        if not t_vi or t_vi == a["title"] or len(t_vi) <= 5:
            missing.append(a)

    if not missing:
        return partial_result

    missing_text = ""
    for a in missing:
        flag = "EN" if a.get("lang") == "en" else "VI"
        missing_text += f"\n[{a['id']}|{flag}] {a['title']}\n{a['summary'][:80]}\n---"

    retry_prompt = f"""Bổ sung {len(missing)} bài còn thiếu. Dùng đúng ID:

{missing_text}

JSON: {{"articles_vi": [{{"id": "...", "title_vi": "...", "summary_vi": "..."}}]}}

JSON:"""

    caller = PROVIDER_CALLERS[provider_name]
    try:
        with hard_timeout(130):
            text, _ = caller(model, retry_prompt)
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


def process_with_ai(articles):
    total = len(articles)
    batches = [articles[i:i + MAX_ARTICLES_FOR_AI] for i in range(0, total, MAX_ARTICLES_FOR_AI)]
    print(f"  → Tổng {total} bài, chia thành {len(batches)} batch (mỗi batch ≤{MAX_ARTICLES_FOR_AI} bài)")

    merged_articles_vi = []
    merged_clusters = []
    merged_trends = []
    digest_result = None

    for i, batch in enumerate(batches, 1):
        label = f" [batch {i}/{len(batches)}, {len(batch)} bài]"
        batch_result = process_batch_with_ai(batch, batch_label=label)

        # Tăng delay từ 1s → 5s để tránh Gemini 429
        if i < len(batches):
            time.sleep(5)

        merged_articles_vi.extend(batch_result.get("articles_vi", []))
        merged_clusters.extend(batch_result.get("clusters", []))
        merged_trends.extend(batch_result.get("trends", []))

        if i == 1:
            digest_result = batch_result.get("digest")

    valid_trends = [t for t in merged_trends if isinstance(t, dict)]
    dropped = len(merged_trends) - len(valid_trends)
    if dropped:
        print(f"  ⚠️  Loại {dropped} trend không đúng định dạng")
    valid_trends.sort(key=lambda t: t.get("score", 0), reverse=True)
    merged_trends = valid_trends[:5]
    for idx, t in enumerate(merged_trends, 1):
        t["rank"] = idx

    if not digest_result:
        digest_result = fallback_result()["digest"]

    merged_articles_vi = [a for a in merged_articles_vi if isinstance(a, dict) and "id" in a]
    merged_clusters    = [c for c in merged_clusters if isinstance(c, dict) and "articles" in c]

    print(f"  ✅ Tổng hợp {len(batches)} batch: {len(merged_articles_vi)} bài dịch, {len(merged_clusters)} clusters, {len(merged_trends)} trends")

    return {
        "articles_vi": merged_articles_vi,
        "clusters":    merged_clusters,
        "trends":      merged_trends,
        "digest":      digest_result,
    }


# ─── VALIDATE TRANSLATION MATCH ───────────────────────────────
def extract_signature_tokens(text):
    if not text:
        return set()
    numbers = set(re.findall(r"\d[\d.,]*", text))
    proper_nouns = set(re.findall(r"[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*", text))
    return numbers | proper_nouns


def translation_looks_mismatched(orig_title, orig_summary, t_vi, s_vi):
    orig_sig = extract_signature_tokens(orig_title + " " + orig_summary)
    if not orig_sig:
        return False
    vi_sig = extract_signature_tokens(t_vi + " " + s_vi)
    overlap = orig_sig & vi_sig
    return len(overlap) == 0


# ─── MERGE TRANSLATIONS ────────────────────────────────────────
def merge_translations(articles, ai_result):
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

            mismatched = translation_looks_mismatched(a["title"], a["summary"], t_vi, s_vi)
            if mismatched and en_total <= 10:
                print(f"     ⚠️  Nghi ngờ gán nhầm nội dung")

            if t_vi and t_vi != a["title"] and len(t_vi) > 5 and not mismatched:
                a["title_vi"] = t_vi
                a["summary_vi"] = s_vi if s_vi else a["summary"]
                en_translated += 1
            else:
                a["title_vi"] = a["title"]
                a["summary_vi"] = a["summary"]
                en_fallback += 1
                reason = "mismatched" if mismatched else "missing/empty"
                en_missing_details.append({"id": a["id"], "source": a["source"], "title": a["title"][:50], "reason": reason})
        else:
            a["title_vi"] = a["title"]
            vi = lookup.get(a["id"], {})
            s_vi = (vi.get("summary_vi") or "").strip()
            if s_vi and not translation_looks_mismatched(a["title"], a["summary"], a["title"], s_vi):
                a["summary_vi"] = s_vi
            else:
                a["summary_vi"] = a["summary"]

    mismatch_count = sum(1 for item in en_missing_details if item.get("reason") == "mismatched")
    print(f"  📊 Dịch EN→VI: {en_translated}/{en_total} bài (fallback: {en_fallback}, trong đó {mismatch_count} bị nghi gán nhầm)")
    if en_fallback > 0:
        print(f"  ⚠️  {en_fallback} bài EN thiếu/nghi nhầm:")
        for item in en_missing_details[:5]:
            print(f"     [{item['id']}] ({item.get('reason','?')}) {item['source']}: {item['title']}")

    return articles


# ─── GOOGLE TRENDS ────────────────────────────────────────────
def fetch_google_trends():
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

    print("🔑 API keys status:")
    for key in ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]:
        val = os.environ.get(key)
        status = f"✅ có ({len(val)} ký tự)" if val else "❌ KHÔNG CÓ"
        print(f"   • {key}: {status}")

    print("\n🔌 AI providers đã cấu hình (theo thứ tự ưu tiên):")
    for p in AI_PROVIDERS:
        print(f"   • {p['name']}: {len(p['models'])} models")

    print("\n1️⃣  Init Firebase...")
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

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
    # Strip markdown code blocks
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*",     "", text)
    text = re.sub(r"\s*```$",     "", text)
    text = text.strip()

    # Tìm JSON object trong text (phòng khi AI có text thừa)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)

    return json.loads(text)


def process_with_ai(articles):
    print("  → Gọi OpenRouter AI...")
    prompt = build_prompt(articles)
    subset_count = len(articles[:MAX_ARTICLES_FOR_AI])

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
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        print(f"  → AI raw response length: {len(text)} chars")

        result = parse_ai_response(text)

        # Kiểm tra articles_vi có đủ không
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
        print(f"  Response text (đầu): {text[:500] if 'text' in dir() else 'N/A'}")
        return fallback_result()
    except Exception as e:
        print(f"  ❌ AI lỗi: {e}")
        if 'resp' in locals() and hasattr(resp, 'text'):
            print(f"  Response: {resp.text[:500]}")
        return fallback_result()


def retry_ai(articles, original_prompt, partial_result):
    """Retry khi AI trả về thiếu articles_vi"""
    print("  → Retry với prompt bổ sung...")
    subset_count = len(articles[:MAX_ARTICLES_FOR_AI])
    existing_indices = [a.get("index") for a in partial_result.get("articles_vi", [])]
    missing = [i for i in range(1, subset_count + 1) if i not in existing_indices]

    # Tạo danh sách bài thiếu
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
                "max_tokens":  4000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        retry_data = parse_ai_response(text)

        # Merge kết quả retry vào result cũ
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
    """Gộp bản dịch AI vào từng article - robust, có fallback"""
    arts_vi = ai_result.get("articles_vi", [])

    # Tạo lookup theo index (cả int và str)
    lookup = {}
    for item in arts_vi:
        idx = item.get("index")
        if idx is not None:
            lookup[int(idx)] = item

    en_total = sum(1 for a in articles[:MAX_ARTICLES_FOR_AI] if a["lang"] == "en")
    en_missing = 0

    for i, a in enumerate(articles[:MAX_ARTICLES_FOR_AI], 1):
        vi = lookup.get(i, {})

        if a["lang"] == "en":
            # Bài tiếng Anh - cần dịch từ AI
            t_vi = (vi.get("title_vi") or "").strip()
            s_vi = (vi.get("summary_vi") or "").strip()

            if t_vi:
                a["title_vi"] = t_vi
            else:
                a["title_vi"] = a["title"]  # fallback: giữ nguyên tiếng Anh
                en_missing += 1

            if s_vi:
                a["summary_vi"] = s_vi
            else:
                a["summary_vi"] = a["summary"]
        else:
            # Bài tiếng Việt - giữ nguyên
            a["title_vi"]   = a["title"]
            a["summary_vi"] = a["summary"]

    if en_missing > 0:
        print(f"  ⚠️  {en_missing}/{en_total} bài EN thiếu bản dịch (dùng gốc)")
    else:
        print(f"  ✅ Dịch thành công {en_total} bài EN → VI")

    return articles

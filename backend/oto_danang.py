"""
Xu hướng tìm kiếm ô tô ở Đà Nẵng — module phụ của tin247.

Ghép vào scraper chính bằng 4 dòng (xem cuối file).
Chạy mặc định vào thứ Hai hàng tuần, vì dữ liệu Google Trends 12 tháng
là theo tuần, chạy mỗi ngày vừa không đổi vừa dễ bị Google chặn.
"""

import json
import os
import random
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

GEO = "VN-DN"             # Đà Nẵng. Cả nước thì đổi thành "VN"
NODE = "oto_danang"       # node trên Realtime Database
TIMEFRAME = "today 12-m"
BATCH_SIZE = 4            # Google cho tối đa 5 từ/lần, chừa 1 chỗ cho từ neo
SLEEP_MIN, SLEEP_MAX = 12, 20
MAX_RETRY = 3
NGAY_CHAY = 0             # 0 = thứ Hai. Đổi thành None để chạy mỗi ngày

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"     {msg}", flush=True)


# ─── ĐỌC DANH SÁCH TỪ KHÓA ────────────────────────────────────
def load_keywords():
    path = os.path.join(HERE, "oto_keywords.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── GOOGLE TRENDS ────────────────────────────────────────────
def new_client():
    """
    KHÔNG truyền retries/backoff_factor: pytrends sẽ dựng urllib3 Retry với
    tham số method_whitelist đã bị bỏ ở urllib3 mới → TypeError.
    Giữ đúng kiểu khởi tạo như fetch_trends_detail() trong scraper.
    """
    from pytrends.request import TrendReq
    return TrendReq(hl="vi-VN", tz=420, timeout=(10, 30))


def fetch_batch(pytrends, words):
    for attempt in range(1, MAX_RETRY + 1):
        try:
            pytrends.build_payload(words, cat=0, timeframe=TIMEFRAME, geo=GEO, gprop="")
            df = pytrends.interest_over_time()
            if df is None or df.empty:
                return {}, [], pytrends
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
            series = {w: [int(v) for v in df[w].tolist()] for w in df.columns}
            return series, dates, pytrends
        except Exception as e:
            wait = attempt * 30
            log(f"⚠️  {e.__class__.__name__}: {str(e)[:200]}")
            log(f"   thử lại sau {wait}s [{attempt}/{MAX_RETRY}]")
            time.sleep(wait)
            pytrends = new_client()
    log(f"❌ bỏ qua nhóm: {words}")
    return {}, [], pytrends


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def collect(nhom_tu_khoa, anchor):
    """
    Google chuẩn hóa điểm riêng cho từng lần gọi, nên điểm giữa các lần gọi
    vốn không so sánh được. Cách xử lý: mọi lần gọi đều kèm một từ neo,
    rồi quy tất cả về cùng một thang theo từ neo đó.
    """
    pytrends = new_client()
    flat = [(kw, nhom) for nhom, kws in nhom_tu_khoa.items() for kw in kws]
    batches = [flat[i:i + BATCH_SIZE] for i in range(0, len(flat), BATCH_SIZE)]
    log(f"{len(flat)} từ khóa, {len(batches)} lượt gọi")

    results, all_dates, anchor_ref = {}, [], None

    for i, batch in enumerate(batches, 1):
        words = [kw for kw, _ in batch]
        series, dates, pytrends = fetch_batch(pytrends, [anchor] + words)

        if series:
            if not all_dates:
                all_dates = dates
            anchor_mean = mean(series.get(anchor, []))
            if anchor_ref is None:
                anchor_ref = anchor_mean if anchor_mean > 0 else 1.0
                factor = 1.0
            else:
                factor = (anchor_ref / anchor_mean) if anchor_mean > 0 else 1.0

            for kw, nhom in batch:
                if kw in series:
                    results[kw] = {
                        "kw": kw,
                        "nhom": nhom,
                        "series": [round(v * factor, 2) for v in series[kw]],
                    }

        if i % 5 == 0 or i == len(batches):
            log(f"… {i}/{len(batches)} lượt")
        if i < len(batches):
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    return results, all_dates


def summarize(results):
    items = []
    for data in results.values():
        s = data["series"]
        if len(s) < 12:
            continue
        gan_day = mean(s[-4:])
        truoc_do = mean(s[-12:-8])
        thay_doi = round((gan_day - truoc_do) / truoc_do * 100, 1) if truoc_do > 0 else 0.0
        items.append({**data, "diem": round(gan_day, 1),
                      "dinh": round(max(s), 1), "thayDoi": thay_doi})
    items.sort(key=lambda x: x["diem"], reverse=True)
    for rank, it in enumerate(items, 1):
        it["hang"] = rank
    return items


# ─── GOOGLE GỢI Ý ─────────────────────────────────────────────
def fetch_suggest(seed):
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "hl": "vi", "gl": "vn", "q": seed},
            timeout=15, headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        return r.json()[1][:10]
    except Exception:
        return []


# ─── NHẬN ĐỊNH BẰNG AI ────────────────────────────────────────
def build_prompt(items, goi_y):
    tang = sorted([i for i in items if i["diem"] >= 3],
                  key=lambda x: -x["thayDoi"])[:8]
    giam = sorted([i for i in items if i["diem"] >= 3],
                  key=lambda x: x["thayDoi"])[:5]
    cao = items[:10]

    def dong(ds, co_thay_doi=True):
        return "\n".join(
            f"- {i['kw']} ({i['nhom']}): {i['diem']} điểm"
            + (f", {'+' if i['thayDoi'] > 0 else ''}{i['thayDoi']}% so với 2 tháng trước" if co_thay_doi else "")
            for i in ds
        ) or "(không có)"

    gy = "\n".join(f"- {g['tu']}: {', '.join(g['danhSach'][:5])}"
                   for g in goi_y if g.get("danhSach")) or "(không có)"

    return f"""Bạn tư vấn cho chủ một tiệm phục hồi mâm xe ô tô ở Đà Nẵng.
Dưới đây là mức độ quan tâm tìm kiếm trên Google tại Đà Nẵng (thang tương đối,
KHÔNG phải số lượt tìm thật).

## Tìm nhiều nhất
{dong(cao)}

## Đang tăng
{dong(tang)}

## Đang giảm
{dong(giam)}

## Cách người ta gõ khi tìm (Google gợi ý)
{gy}

Trả về CHỈ JSON, không markdown:
{{
  "nhan_dinh": "3-4 câu nhận xét thị trường ô tô Đà Nẵng tuần này, dựa đúng số liệu trên",
  "nen_lam": [
    {{"viec": "Việc nên làm cụ thể", "vi_sao": "Dựa trên từ khóa nào, 1 câu"}}
  ],
  "y_tuong_bai_viet": ["Tiêu đề bài viết nên đăng lên web tiệm"]
}}

Yêu cầu:
- "nen_lam": 3-4 việc, thiên về dịch vụ mâm xe và ô tô nói chung, làm được ở quy mô tiệm nhỏ.
- "y_tuong_bai_viet": 4-5 tiêu đề, bám sát cụm từ Google gợi ý ở trên để dễ lên top.
- Không bịa số liệu ngoài dữ liệu đã cho.
"""


def ai_nhan_dinh(items, goi_y, providers, parse_fn):
    if not providers:
        return None
    prompt = build_prompt(items, goi_y)
    for pname, caller in providers:
        try:
            text, _ = caller(prompt)
            result = parse_fn(text)
            if isinstance(result, dict) and result.get("nhan_dinh"):
                log(f"🤖 nhận định bởi {pname}")
                return result
        except Exception as e:
            log(f"⚠️  {pname} lỗi: {str(e)[:100]}")
            continue
    return None


# ─── CHẠY ─────────────────────────────────────────────────────
def run(ref, providers=None, parse_fn=None, force=False):
    """
    ref       : db.reference() từ scraper chính
    providers : danh sách PROVIDERS của scraper, để nhờ AI viết nhận định
    parse_fn  : hàm parse_ai_response của scraper
    force     : True thì chạy bất kể hôm nay là thứ mấy
    """
    now = datetime.now(VN_TZ)

    if not force and NGAY_CHAY is not None and now.weekday() != NGAY_CHAY:
        log(f"hôm nay không phải ngày chạy (chỉ chạy thứ {NGAY_CHAY + 2}), bỏ qua")
        return

    cfg = load_keywords()
    results, dates = collect(cfg["nhom"], cfg.get("anchor", "ô tô"))

    if not results:
        log("❌ không lấy được dữ liệu, giữ nguyên dữ liệu cũ")
        return

    items = summarize(results)
    log(f"✅ {len(items)} từ khóa")

    goi_y = []
    for seed in cfg.get("goi_y_tu_google", []):
        ds = fetch_suggest(seed)
        if ds:
            goi_y.append({"tu": seed, "danhSach": ds})
        time.sleep(1.5)
    log(f"💡 gợi ý cho {len(goi_y)} cụm từ")

    nhan_dinh = None
    if providers and parse_fn:
        nhan_dinh = ai_nhan_dinh(items, goi_y, providers, parse_fn)

    payload = {
        "capNhat": now.isoformat(),
        "capNhatHienThi": now.strftime("%d/%m/%Y %H:%M"),
        "khuVuc": "Đà Nẵng",
        "khoangThoiGian": "12 tháng gần nhất",
        "ngay": dates,
        "tuKhoa": items,
        "goiY": goi_y,
    }
    if nhan_dinh:
        payload["nhanDinh"] = nhan_dinh

    ref.child(f"{NODE}/snapshot").set(payload)
    ref.child(f"{NODE}/lichsu/{now.strftime('%Y-%m-%d')}").set({
        "capNhat": payload["capNhat"],
        "tuKhoa": {str(i["hang"]): {"kw": i["kw"], "diem": i["diem"],
                                    "thayDoi": i["thayDoi"]} for i in items},
    })
    log(f"💾 đã lưu {NODE}/snapshot")


# ─── CHẠY ĐỘC LẬP ─────────────────────────────────────────────
# Workflow riêng (.github/workflows/oto-trends.yml) gọi thẳng file này:
#     python backend/oto_danang.py
# Không đụng gì tới scraper tin tức.

if __name__ == "__main__":
    import firebase_admin
    from firebase_admin import credentials, db

    FIREBASE_DB_URL = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"

    if not firebase_admin._apps:
        sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if not sa:
            raise SystemExit("❌ Thiếu FIREBASE_SERVICE_ACCOUNT")
        credential = credentials.Certificate(json.loads(sa))
        firebase_admin.initialize_app(credential, {"databaseURL": FIREBASE_DB_URL})

    # Mượn lại danh sách AI provider của scraper để viết phần nhận định.
    # Nếu không mượn được thì vẫn chạy bình thường, chỉ thiếu phần nhận định.
    providers, parse_fn = None, None
    try:
        sys.path.insert(0, HERE)
        import scraper
        providers, parse_fn = scraper.PROVIDERS, scraper.parse_ai_response
        log(f"dùng {len(providers)} AI provider của scraper")
    except Exception as e:
        log(f"không mượn được AI provider ({str(e)[:80]}), bỏ qua phần nhận định")

    print("🚗 Xu hướng ô tô Đà Nẵng")
    run(db.reference(), providers=providers, parse_fn=parse_fn, force=True)

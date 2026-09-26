"""
Xu hướng tìm kiếm Google theo khu vực — module dùng chung.

Mỗi chủ đề là một file cấu hình JSON riêng. Chạy:
    python backend/xuhuong.py backend/suckhoe_keywords.json

File cấu hình quyết định: node lưu trên Firebase, khu vực, từ neo,
danh sách từ khóa và bối cảnh cho phần nhận định AI.
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
TIMEFRAME = "today 12-m"
BATCH_SIZE = 4            # Google cho tối đa 5 từ/lần, chừa 1 chỗ cho từ neo
SLEEP_MIN, SLEEP_MAX = 12, 20
MAX_RETRY = 3
FIREBASE_DB_URL = "https://tonghoptinngay-default-rtdb.asia-southeast1.firebasedatabase.app"

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"     {msg}", flush=True)


# ─── GOOGLE TRENDS ────────────────────────────────────────────
def new_client():
    # KHÔNG truyền retries/backoff_factor: pytrends sẽ dựng urllib3 Retry
    # với tham số đã bị bỏ → TypeError (lỗi đã gặp ở bản ô tô).
    from pytrends.request import TrendReq
    return TrendReq(hl="vi-VN", tz=420, timeout=(10, 30))


def fetch_batch(pytrends, words, geo):
    for attempt in range(1, MAX_RETRY + 1):
        try:
            pytrends.build_payload(words, cat=0, timeframe=TIMEFRAME, geo=geo, gprop="")
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


def collect(nhom_tu_khoa, anchor, geo):
    """
    Google chuẩn hóa điểm riêng cho từng lần gọi, nên mọi lần gọi đều
    kèm một từ neo rồi quy tất cả về cùng một thang theo từ neo đó.
    """
    pytrends = new_client()
    flat = [(kw, nhom) for nhom, kws in nhom_tu_khoa.items() for kw in kws]
    batches = [flat[i:i + BATCH_SIZE] for i in range(0, len(flat), BATCH_SIZE)]
    log(f"{len(flat)} từ khóa, {len(batches)} lượt gọi, từ neo: '{anchor}'")

    results, all_dates, anchor_ref = {}, [], None
    anchor_rong = 0

    for i, batch in enumerate(batches, 1):
        words = [kw for kw, _ in batch]
        series, dates, pytrends = fetch_batch(pytrends, [anchor] + words, geo)

        if series:
            if not all_dates:
                all_dates = dates
            anchor_mean = mean(series.get(anchor, []))
            if anchor_mean == 0:
                anchor_rong += 1
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

    if anchor_rong:
        log(f"⚠️  từ neo '{anchor}' bằng 0 ở {anchor_rong} lượt — nên đổi từ neo khác, thang điểm lượt đó không quy đổi được")
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
def build_prompt(items, goi_y, cfg):
    du_lieu = [i for i in items if i["diem"] >= 3]
    tang = sorted(du_lieu, key=lambda x: -x["thayDoi"])[:8]
    giam = sorted(du_lieu, key=lambda x: x["thayDoi"])[:5]
    cao = items[:10]

    def dong(ds):
        return "\n".join(
            f"- {i['kw']} ({i['nhom']}): {i['diem']} điểm, "
            f"{'+' if i['thayDoi'] > 0 else ''}{i['thayDoi']}% so với 2 tháng trước"
            for i in ds
        ) or "(không có)"

    gy = "\n".join(f"- {g['tu']}: {', '.join(g['danhSach'][:5])}"
                   for g in goi_y if g.get("danhSach")) or "(không có)"

    boi_canh = cfg.get("boi_canh_ai", "Phân tích xu hướng tìm kiếm.")
    khu_vuc = cfg.get("khu_vuc", "Đà Nẵng")

    return f"""{boi_canh}

Dưới đây là mức độ quan tâm tìm kiếm trên Google tại {khu_vuc}
(thang tương đối, KHÔNG phải số lượt tìm thật).

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
  "nhan_dinh": "3-4 câu nhận xét xu hướng tuần này, dựa đúng số liệu trên",
  "nen_lam": [
    {{"viec": "Cơ hội hoặc việc nên làm cụ thể", "vi_sao": "Dựa trên từ khóa nào, 1 câu"}}
  ],
  "y_tuong_bai_viet": ["Tiêu đề nội dung nên làm"]
}}

Yêu cầu:
- "nen_lam": 3-4 mục.
- "y_tuong_bai_viet": 4-5 tiêu đề, bám sát cụm từ Google gợi ý ở trên.
- Không bịa số liệu ngoài dữ liệu đã cho.
- Không đưa lời khuyên chẩn đoán hay điều trị y tế; đây là phân tích xu hướng tìm kiếm.
"""


def ai_nhan_dinh(items, goi_y, cfg, providers, parse_fn):
    if not providers or not parse_fn:
        return None
    prompt = build_prompt(items, goi_y, cfg)
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
def run(ref, cfg, providers=None, parse_fn=None):
    now = datetime.now(VN_TZ)
    node = cfg["node"]
    geo = cfg.get("geo", "VN-DN")

    results, dates = collect(cfg["nhom"], cfg.get("anchor", ""), geo)
    if not results:
        log("❌ không lấy được dữ liệu, giữ nguyên dữ liệu cũ")
        return False

    items = summarize(results)
    log(f"✅ {len(items)} từ khóa")

    goi_y = []
    for seed in cfg.get("goi_y_tu_google", []):
        ds = fetch_suggest(seed)
        if ds:
            goi_y.append({"tu": seed, "danhSach": ds})
        time.sleep(1.5)
    log(f"💡 gợi ý cho {len(goi_y)} cụm từ")

    nhan_dinh = ai_nhan_dinh(items, goi_y, cfg, providers, parse_fn)

    payload = {
        "capNhat": now.isoformat(),
        "capNhatHienThi": now.strftime("%d/%m/%Y %H:%M"),
        "khuVuc": cfg.get("khu_vuc", "Đà Nẵng"),
        "chuDe": cfg.get("chu_de", ""),
        "khoangThoiGian": "12 tháng gần nhất",
        "ngay": dates,
        "tuKhoa": items,
        "goiY": goi_y,
    }
    if nhan_dinh:
        payload["nhanDinh"] = nhan_dinh

    ref.child(f"{node}/snapshot").set(payload)
    ref.child(f"{node}/lichsu/{now.strftime('%Y-%m-%d')}").set({
        "capNhat": payload["capNhat"],
        "tuKhoa": {str(i["hang"]): {"kw": i["kw"], "diem": i["diem"],
                                    "thayDoi": i["thayDoi"]} for i in items},
    })
    log(f"💾 đã lưu {node}/snapshot")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Cách chạy: python backend/xuhuong.py <file_cau_hinh.json>")

    duong_dan = sys.argv[1]
    if not os.path.isabs(duong_dan) and not os.path.exists(duong_dan):
        duong_dan = os.path.join(HERE, os.path.basename(duong_dan))
    with open(duong_dan, encoding="utf-8") as f:
        cfg = json.load(f)

    import firebase_admin
    from firebase_admin import credentials, db

    if not firebase_admin._apps:
        sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if not sa:
            raise SystemExit("❌ Thiếu FIREBASE_SERVICE_ACCOUNT")
        firebase_admin.initialize_app(credentials.Certificate(json.loads(sa)),
                                      {"databaseURL": FIREBASE_DB_URL})

    # Mượn danh sách AI provider của scraper tin247 để viết phần nhận định
    providers, parse_fn = None, None
    try:
        sys.path.insert(0, HERE)
        import scraper
        providers, parse_fn = scraper.PROVIDERS, scraper.parse_ai_response
        log(f"dùng {len(providers)} AI provider của scraper")
    except Exception as e:
        log(f"không mượn được AI provider ({str(e)[:80]}), bỏ qua phần nhận định")

    print(f"📈 Xu hướng: {cfg.get('chu_de', cfg['node'])}")
    ok = run(db.reference(), cfg, providers=providers, parse_fn=parse_fn)
    sys.exit(0 if ok else 1)

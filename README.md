# 📰 Tin247 — Tổng Hợp Tin Tức AI

Hệ thống tự động scrape RSS, phân tích bằng Gemini AI, lưu Firebase, hiển thị dashboard.

---

## 🏗️ Cấu trúc

```
news-digest/
├── backend/
│   ├── scraper.py          # Scrape RSS + Gemini AI + Firebase
│   └── requirements.txt
├── frontend/
│   ├── index.html          # Dashboard chính (public)
│   └── admin.html          # Trang quản trị (cần login)
├── .github/
│   └── workflows/
│       └── daily.yml       # GitHub Actions cron
├── database.rules.json     # Firebase security rules
└── README.md
```

---

## ⚙️ Setup từng bước

### 1. Firebase

**a. Tạo Realtime Database**
- Vào [Firebase Console](https://console.firebase.google.com) → dự án `tonghoptinngay`
- Build → Realtime Database → Create database
- Chọn region: `asia-southeast1`
- Chọn mode: **Test mode** (sẽ update rules sau)

**b. Update Security Rules**
- Tab Rules → paste nội dung file `database.rules.json`
- Publish

**c. Tạo Service Account (cho Python)**
- Project Settings → Service accounts → Generate new private key
- Tải file JSON về
- Convert sang 1 dòng: `cat serviceAccount.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"`
- Đây là giá trị cho secret `FIREBASE_SERVICE_ACCOUNT`

**d. Tạo Firebase Auth user (cho admin.html)**
- Build → Authentication → Get started → Email/Password → Enable
- Users → Add user → nhập email + password của anh

### 2. GitHub Repository

**a. Tạo repo mới** trên github.com (public hoặc private đều được)

**b. Push code**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/news-digest.git
git push -u origin main
```

**c. Thêm Secrets**
- Repo → Settings → Secrets and variables → Actions → New repository secret

| Secret name | Giá trị |
|---|---|
| `GEMINI_API_KEY` | AIzaSy... (Gemini API key) |
| `FIREBASE_SERVICE_ACCOUNT` | `{"type":"service_account",...}` (JSON 1 dòng) |

### 3. Deploy Frontend

**Option A — Firebase Hosting (recommended)**
```bash
npm install -g firebase-tools
firebase login
firebase init hosting    # public dir: frontend
firebase deploy
```

**Option B — GitHub Pages**
- Repo Settings → Pages → Deploy from branch → main → /frontend

### 4. Test thủ công

Sau khi setup xong, trigger thủ công lần đầu:
- GitHub repo → Actions tab → "News Digest Daily" → Run workflow

Chờ ~3 phút → xem log → nếu xanh thì mở `index.html` kiểm tra.

---

## 📅 Lịch tự động

| Thời gian | Giờ VN |
|---|---|
| 6:00 SA | Scrape buổi sáng |
| 12:00 TR | Scrape buổi trưa |

Có thể thêm 6:00 CH bằng cách thêm cron `0 11 * * *` vào `daily.yml`.

---

## 🗄️ Cấu trúc Firebase DB

```
tonghoptinngay-default-rtdb/
├── articles/
│   └── 2025-01-15/
│       └── {articleId}/
│           ├── id, title, summary, url
│           ├── source, lang, cat
│           └── pubDate, date
├── clusters/
│   └── 2025-01-15/ → array of clusters
├── trends/
│   └── 2025-01-15/ → array of trends
├── digest/
│   └── 2025-01-15/
│       ├── headline, overview
│       ├── key_points[]
│       ├── date, updatedAt
│       └── totalArticles
└── meta/
    ├── lastUpdated
    └── lastDate
```

---

## 🔧 Tuỳ chỉnh

**Thêm nguồn RSS**: Sửa `RSS_SOURCES` trong `scraper.py`

**Đổi giờ chạy**: Sửa cron trong `.github/workflows/daily.yml`

**Đổi số bài gửi AI**: Sửa `MAX_ARTICLES_FOR_AI` trong `scraper.py` (mặc định 40)

**Gemini model**: Đổi `gemini-2.0-flash` thành `gemini-1.5-pro` nếu cần kết quả tốt hơn
"# tintuc" 

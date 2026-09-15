# Crawl E-Commerce API

FastAPI service crawl dữ liệu thương mại điện tử: Google Trends, Facebook Ads Library, và AI price extraction.

---

## Kiến trúc tổng quan

```
Client
  └─► FastAPI (app/main.py)
        ├─ /api/v1/trends      → TrendsService → ProviderManager
        │                                          ├─ SerpAPI (key pool từ .env)
        │                                          ├─ Direct pytrends (fallback)
        │                                          └─ Direct pytrends + Proxy (fallback cuối)
        ├─ /api/v1/ads         → ApifyClient (Facebook Ad Library Actor)
        ├─ /api/v1/ai          → WebFetcher + OpenRouterClient (free LLM)
        ├─ /api/v1/providers   → Quản lý SerpAPI key status + Proxy DB
        └─ /api/v1/audit-logs  → Đọc audit log
```

---

## Các Service

### 1. Google Trends (`/api/v1/trends`)

Lấy dữ liệu xu hướng từ Google Trends.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/presets` | Danh sách time preset (7d, 30d, 90d, 1y, 5y…) |
| `POST` | `/interest-over-time` | Trend interest theo thời gian cho 1–5 từ khóa |
| `POST` | `/related-queries` | Các từ khóa liên quan (top + rising) |

**Fallback cascade (ProviderManager):**
1. **SerpAPI** — gọi `https://serpapi.com/search` với key từ pool, tự xoay key khi 429
2. **Direct Google (pytrends)** — không cần key, nhưng dễ bị Google block
3. **Direct Google + Proxy** — lấy proxy từ DB, rotate khi bị block

Kết quả được lưu vào bảng `trends_results` và `audit_logs`.

**ENV cần:** `SERPAPI_KEYS`, `DATABASE_URL`

---

### 2. Facebook Ads (`/api/v1/ads`)

Scrape Facebook Ads Library qua Apify actor `igolaizola~facebook-ad-library-scraper`.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/search` | Chạy actor Apify, poll cho đến khi xong, trả về danh sách ads |
| `GET` | `` (root) | Xem lại kết quả đã lưu trong DB |

**Luồng:** Start actor → poll status mỗi 3 giây (timeout 300s) → fetch dataset items → lưu vào `facebook_ads_results` + `audit_logs`.

**ENV cần:** `APIFY_TOKEN`, `DATABASE_URL`

---

### 3. AI Analysis (`/api/v1/ai`)

Crawl URL bất kỳ và dùng AI (OpenRouter) để extract thông tin giá/sản phẩm.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/analyze` | Crawl URL → làm sạch HTML → gửi LLM → trả về JSON sản phẩm/giá |
| `GET` | `/models` | Danh sách free models trên OpenRouter |
| `GET` | `/master-prompt` | Xem prompt đang dùng (lưu trong DB) |
| `PUT` | `/master-prompt` | Cập nhật system prompt / user template |
| `GET` | `/keys/status` | Trạng thái OpenRouter key pool |
| `GET` | `/serpapi/status` | Trạng thái SerpAPI key pool |
| `GET` | `/results` | Danh sách kết quả đã phân tích |

**Luồng:** `fetch_page_text` (httpx + BeautifulSoup, tối đa 12.000 ký tự) → lấy prompt từ DB → gọi OpenRouter (rotate key khi 429, tối đa 3 lần) → parse JSON → lưu `ai_analysis_results`.

**Free models mặc định:** `nvidia/nemotron-3.5-lightning:free`, và 5 model khác.

**ENV cần:** `OPENROUTER_API_KEYS`, `DATABASE_URL`

---

### 4. Provider Management (`/api/v1/providers`)

Quản lý nguồn data: xem trạng thái SerpAPI keys và thêm/xoá proxy.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/status` | SerpAPI: số lượng key, lượt tìm còn lại từng key |
| `POST` | `/proxies` | Thêm proxy (`http://user:pass@host:port`) |
| `GET` | `/proxies` | Danh sách proxy trong DB |
| `PATCH` | `/proxies/{id}/toggle` | Bật/tắt proxy |
| `DELETE` | `/proxies/{id}` | Xoá proxy |

> **Note:** SerpAPI keys chỉ đọc từ `.env`, không lưu DB. Proxy vẫn lưu DB vì IP rotate độc lập với key.

---

### 5. Browser Profiles (`/api/v1/browser`)

Multi-account browser automation qua **CloakBrowser** — Chromium với 73 C++ patches, bypass Cloudflare/Datadome/FingerprintJS.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/profiles` | Tạo profile mới (tự sinh fingerprint seed) |
| `GET` | `/profiles` | List profiles |
| `GET` | `/profiles/{id}` | Chi tiết profile |
| `PATCH` | `/profiles/{id}` | Update proxy/label/notes |
| `DELETE` | `/profiles/{id}` | Xoá profile |
| `POST` | `/profiles/{id}/run` | Chạy task crawl URL với profile này |
| `GET` | `/pool/status` | Xem concurrent limit + RAM estimate |

**Kiến trúc multi-account:**
- 1 Docker container `cloakserve` → N Chrome process qua `fingerprint_seed` khác nhau
- Mỗi account = 1 profile trong DB (seed + proxy + profile_dir riêng)
- `fingerprint_seed` nhất quán: same seed = same canvas/WebGL/fonts mỗi lần launch
- `browser_pool` (semaphore) giới hạn concurrent theo license tier

**RAM per account:** ~190MB idle, ~280MB với 3 tab, ~30MB mỗi tab thêm

**ENV cần:** `CLOAK_BROWSER_URL`, `CLOAK_LICENSE_KEY` (để trống = free, 1 concurrent)

---

### 6. Audit Logs (`/api/v1/audit-logs`)

Ghi lại toàn bộ request/response của mọi endpoint.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `` (root) | List logs, filter theo endpoint / status |
| `GET` | `/{request_id}` | Chi tiết 1 log theo request_id |

---

## ENV Variables

| Biến | Bắt buộc | Mô tả |
|------|----------|-------|
| `DATABASE_URL` | **Bắt buộc** | PostgreSQL async: `postgresql+asyncpg://user:pass@host:5432/db` |
| `SERPAPI_KEYS` | Nên có | Comma-separated keys: `key1,key2,key3`. Nếu không có → skip thẳng sang pytrends |
| `APIFY_TOKEN` | Cho Ads | Token Apify. Nếu không có → `/ads/search` trả lỗi 502 |
| `OPENROUTER_API_KEYS` | Cho AI | Comma-separated OpenRouter keys. Nếu không có → `/ai/analyze` lỗi |
| `CLOAK_BROWSER_URL` | Cho Browser | CDP endpoint cloakserve. Default: `http://cloakbrowser:9222` |
| `CLOAK_LICENSE_KEY` | Không | License key CloakBrowser Pro (trống = free, 1 concurrent session) |
| `APP_ENV` | Không | `development` (default) hoặc `production` |
| `APP_DEBUG` | Không | `false` (default) |

### Thiếu ENV nào thì ảnh hưởng gì?

| Thiếu | Hậu quả |
|-------|---------|
| `DATABASE_URL` sai | App không start được (fail kết nối DB) |
| Không có `SERPAPI_KEYS` | Google Trends tự fallback sang pytrends trực tiếp (chậm hơn, dễ bị block) |
| Không có `APIFY_TOKEN` | `POST /ads/search` trả `502 APIFY_TOKEN not configured` |
| Không có `OPENROUTER_API_KEYS` | `POST /ai/analyze` trả `502 No OpenRouter API key available` |

---

## Chạy local

```bash
cp .env.example .env
# Điền các key vào .env

# Khởi động PostgreSQL (hoặc dùng docker-compose)
docker-compose up -d db

# Cài dependencies
pip install -r requirements.txt

# Chạy server
uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

---

## Database

Các bảng chính:

| Bảng | Nội dung |
|------|----------|
| `audit_logs` | Log mọi API call (request, response, latency, error) |
| `trends_results` | Kết quả Google Trends |
| `facebook_ads_results` | Kết quả Facebook Ads scrape |
| `ai_analysis_results` | Kết quả AI price extraction |
| `ai_master_prompts` | System prompt + user template (seed khi khởi động) |
| `proxies` | Danh sách proxy (CRUD qua `/api/v1/providers/proxies`) |

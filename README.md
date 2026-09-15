# crawl-ecomerce-

Service thu thập dữ liệu cho **LevelUp — Product R&D Automation**. Đi lấy dữ liệu thật
từ các sàn TMĐT, thư viện quảng cáo và cộng đồng thảo luận, chuẩn hoá rồi phục vụ cho
AI Service và `levelup_be` qua REST.

Service này **không** chấm điểm và **không** gọi LLM để phân tích — đó là việc của
`levelup_be` (scoring, state machine, duyệt) và `levelup_ai` (LLM, sentiment, matching).

- Thiết kế nghiệp vụ: [`docs/DEV-Design-AI-Ecom-Data-Crawling.md`](docs/DEV-Design-AI-Ecom-Data-Crawling.md)
- Thiết kế tầng thu thập: [`docs/DEV-Design-Crawl-Engine.md`](docs/DEV-Design-Crawl-Engine.md)

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · arq + Redis · PostgreSQL 16

---

## Chạy local

```bash
cp .env.example .env        # điền key vào .env — KHÔNG commit file này
docker-compose up --build
```

Compose dựng 5 service: `db` → `migrate` (chạy một lần rồi thoát) → `api` + `worker`, cộng `adminer`.

| | |
| --- | --- |
| API + Swagger | http://localhost:8000/docs |
| Adminer (xem DB) | http://localhost:8080 |
| Postgres | `localhost:5434` |
| Redis | `localhost:6380` |

Chạy không qua Docker thì cần Postgres 16 + Redis 7 sẵn, rồi:

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload           # API
arq app.worker.WorkerSettings           # worker, terminal khác
```

---

## Migrations (Alembic)

Schema do **Alembic** quản lý. Ứng dụng **không** tự tạo bảng lúc khởi động — nếu chưa
migrate thì log cảnh báo và request sẽ lỗi vì thiếu bảng.

Đây là cùng vai trò với TypeORM migrations bên `levelup_be`, chỉ khác tên lệnh:

| `levelup_be` (TypeORM) | ở đây (Alembic) |
| --- | --- |
| `npm run to:generate` | `alembic revision --autogenerate -m "mô tả"` |
| `npm run to:run` | `alembic upgrade head` |
| `npm run to:revert` | `alembic downgrade -1` |
| `src/database/migrations/` | `alembic/versions/` |

```bash
alembic current                 # môi trường này đang ở revision nào
alembic history --verbose       # lịch sử
alembic upgrade head            # áp mọi migration còn thiếu
alembic downgrade -1            # lùi 1 bước
alembic upgrade head --sql      # chỉ in SQL ra, không chạy (để review trước)
```

### Quy tắc khi đổi schema

1. Sửa model trong `app/models/`.
2. **Thêm model mới thì thêm một dòng import vào `app/models/__init__.py`.**
   Alembic so sánh `Base.metadata` với DB thật — model không được import sẽ bị coi là
   "đã xoá khỏi code" và migration sẽ sinh ra `op.drop_table(...)`.
3. `alembic revision --autogenerate -m "mô tả ngắn"`
4. **Đọc lại file migration vừa sinh ra trước khi chạy.** Autogenerate đoán khá tốt
   nhưng không hoàn hảo — nó hay bỏ sót đổi tên cột (hiểu thành drop + add, tức là
   **mất dữ liệu**) và không tự sinh data migration.
5. `alembic upgrade head`
6. Commit file migration cùng với thay đổi model.

### Tạo migration đầu tiên

Chỉ làm một lần, và phải làm trên **database rỗng**:

```bash
docker-compose up -d db
docker-compose run --rm migrate alembic revision --autogenerate -m "initial schema"
docker-compose run --rm migrate alembic upgrade head
```

> ⚠️ Nếu DB đã có sẵn bảng do `Base.metadata.create_all` tạo ra ở các phiên bản trước,
> autogenerate sẽ chỉ sinh ra phần **chênh lệch** — migration đầu tiên bị thiếu các bảng cũ,
> và môi trường mới dựng từ đầu sẽ hỏng. Xử lý: xoá schema rồi generate lại
> (dữ liệu giai đoạn này là bỏ đi được).
>
> ```bash
> docker-compose exec db psql -U postgres -d crawl_ecomerce \
>   -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
> ```

---

## Các nguồn dữ liệu

| Nguồn | Cách lấy | Endpoint | Trạng thái |
| --- | --- | --- | --- |
| Store Shopify đối thủ | `/products.json` công khai | `POST /api/v1/ecom/shopify/scan` | ✅ |
| Bol.com (NL/BE) | Scrape — JSON-LD, CSS dự phòng | `POST /api/v1/ecom/bol/search` | ✅ |
| Reddit (VOC) | OAuth app-only | `POST /api/v1/voc/reddit/collect` | ✅ |
| Meta Ads Library | Apify actor | `POST /api/v1/ads/search` | ✅ |
| Google Trends | SerpAPI, fallback pytrends | `POST /api/v1/trends/interest-over-time` | ✅ |
| AI trích giá 1 URL | OpenRouter | `POST /api/v1/ai/analyze` | ✅ |
| Amazon (DE·FR·IT·ES) | T1 Apify actor → T2 CloakBrowser | `POST /api/v1/ecom/amazon/search` | ⚠️ cần key |
| 1688 (nguồn hàng) | T1 aggregator → T2 CloakBrowser | `POST /api/v1/ecom/1688/search` | ⚠️ cần key |
| Taobao (nguồn hàng) | T1 aggregator → T2 CloakBrowser | `POST /api/v1/ecom/taobao/search` | ⚠️ cần key |
| AliExpress | — | — | chưa có |

Mọi việc quét đều là **job bất đồng bộ**: endpoint trả `202 {job_id}`, poll kết quả qua
`GET /api/v1/ecom/jobs/{job_id}` (hoặc `/api/v1/voc/jobs/{job_id}`).

### Ba nguồn khó: Amazon · 1688 · Taobao

Ba nguồn này **không** đi thẳng như Shopify/Bol.com mà đi qua **chuỗi tier**, do
`app/crawl/engine.py` điều phối:

```
T1 vendor  (mua dữ liệu)   ──thất bại/chưa có key──▶  T2 browser (CloakBrowser)
```

Thứ tự này là quyết định **D1** trong [`docs/DEV-Design-Crawl-Engine.md`](docs/DEV-Design-Crawl-Engine.md):
anti-bot của ba sàn này ở mức đầu tư hàng chục triệu đô, tự scrape đạt ~70% tuần đầu
rồi tụt dần — chi phí thật nằm ở **công dev đi sửa mỗi lần sàn đổi**, không phải tiền
proxy. Tier browser giữ lại làm dự phòng và để **đo mức độ bị chặn**.

Chưa cấu hình key vendor thì `is_available()` của tier đó trả `False` và engine bỏ qua
nó — **không phải lỗi cấu hình**, và cũng không sinh ra dòng `crawl_attempts` rác nào.

| Nguồn | Tier chính | Biến env cần |
| --- | --- | --- |
| Amazon | `vendor:apify` | `APIFY_TOKEN` + `AMAZON_APIFY_ACTOR` |
| 1688 · Taobao | `vendor:aggregator` | `ALIBABA_AGGREGATOR_BASE` / `_KEY` / `_SECRET` |

Tier `browser` cần `pip install playwright cloakbrowser` và container `cloakbrowser`
đang chạy. Thiếu playwright thì tier tự loại mình, app vẫn khởi động bình thường.

**Giới hạn đã biết ở G1** — chưa có identity pool (proxy/cookie xoay vòng), nên:

- Amazon cần residential khớp marketplace → chưa có thì hay `BLOCKED`
- 1688/Taobao cần residential **Trung Quốc đại lục** + tài khoản đăng nhập → gần như
  chắc chắn `BLOCKED` khi chạy tier browser

Đó là kết quả **có thật và được ghi vào `crawl_attempts`**, không phải lỗi im lặng —
và chính những dòng đó là bằng chứng để chốt việc mua tier T1.

Nhịp crawl của ba nguồn được seed sẵn trong `crawl_source_policies` (Amazon 2 luồng /
4s, hai sàn Alibaba 1 luồng / 6s). Chỉnh bằng `UPDATE`, không cần deploy.

### Xuất ra Excel

```bash
curl -OJ "localhost:8000/api/v1/ecom/export.xlsx"
curl -OJ "localhost:8000/api/v1/ecom/export.xlsx?source=bol&min_rating=4"
```

3 sheet: **San pham** · **Lich su gia** · **Reddit VOC** (chỉ tạo khi có dữ liệu).
Giá được quy từ minor unit về đơn vị chính (`8999` → `89,99`) nhưng **giữ kiểu số của
Excel** kèm định dạng tiền tệ theo từng dòng — vẫn lọc/sắp/tính công thức được.

### Kiểm tra nhanh trước khi chạy job

Endpoint `/probe` chạy **đồng bộ, đúng 1 trang** — dùng để xem parser đọc được gì
trước khi enqueue job hàng loạt:

```bash
curl "localhost:8000/api/v1/ecom/shopify/probe?shop_domain=allbirds.com"
curl "localhost:8000/api/v1/ecom/bol/probe?query=keukenmachine"
curl "localhost:8000/api/v1/ecom/amazon/probe?query=luftbefeuchter&marketplace=amazon.de"
curl "localhost:8000/api/v1/ecom/1688/probe?query=便携榨汁机"
curl "localhost:8000/api/v1/ecom/taobao/probe?query=便携榨汁机"
```

Trường `parse_source` cho biết parser đang đi nhánh nào — **luôn nhìn nó trước tiên**:

| Nguồn | Nhánh bền | Nhánh dự phòng (dễ vỡ) | Sửa ở đâu khi hỏng |
| --- | --- | --- | --- |
| Bol.com | `json-ld` | `css` | `app/services/bol_client.py` |
| Amazon | `data-asin` | — | `app/sources/amazon/normalize.py` |
| 1688 | `embedded-json` | `dom-offer-link` | `app/sources/alibaba_1688/normalize.py` |
| Taobao | `g_page_config` | `dom-item-link` | `app/sources/taobao/normalize.py` |

`parse_source: null` + `outcome: PARSE_FAIL` nghĩa là không đọc ra gì — sàn đã đổi cấu
trúc. Kết quả probe có kèm `html_head` (1500 ký tự đầu) để soi ngay tại chỗ.

Sửa xong selector thì chạy lại bộ kiểm tra trên HTML mẫu để chắc không làm hỏng chỗ khác:

```bash
python scripts/check_parsers.py
```

---

## Browser profile (CloakBrowser) — hạ tầng cho tier T2

Nguồn nào chặn HTTP client thường thì phải đi bằng trình duyệt thật. `cloakserve` là
container Chromium đã vá để qua được Cloudflare / Datadome / FingerprintJS; mỗi
"profile" là một danh tính riêng gồm fingerprint seed + proxy + thư mục cookie.

| Method | Endpoint | Việc |
| --- | --- | --- |
| `POST` | `/api/v1/browser/profiles` | Tạo profile (tự sinh seed nếu không truyền) |
| `GET` | `/api/v1/browser/profiles` | Danh sách |
| `PATCH` | `/api/v1/browser/profiles/{id}` | Đổi proxy / nhãn / bật tắt |
| `POST` | `/api/v1/browser/profiles/{id}/run` | Mở một URL bằng profile này |
| `GET` | `/api/v1/browser/pool/status` | Giới hạn session đồng thời |

**`fingerprint_seed` phải cố định theo profile.** Cùng seed thì canvas/WebGL/font sinh
ra giống hệt nhau qua mọi lần chạy — đó mới là "một máy thật dùng nhiều lần". Đổi seed
mỗi lần mở là tự khai mình là bot.

RAM ~190MB mỗi session lúc rảnh, ~280MB với 3 tab. Số session song song bị chặn bởi
`browser_pool` theo tier license (free = 1).

```bash
docker-compose up -d cloakbrowser
curl "localhost:8000/api/v1/browser/pool/status"
```

---

## Phân loại kết quả

Không gói mọi thất bại vào một rọ — mỗi loại có hành động khác nhau
(`app/crawl/outcomes.py`):

| Outcome | Nghĩa | Hành động |
| --- | --- | --- |
| `OK` | Có dữ liệu | lưu |
| `EMPTY` | Hợp lệ, 0 kết quả | **không retry** — đây là câu trả lời |
| `RATE_LIMITED` | 429 / 430 | chờ rồi thử lại |
| `BLOCKED` | Captcha, 403, store đặt mật khẩu | đổi identity rồi thử lại |
| `NOT_AVAILABLE` | 404, endpoint bị tắt | **không retry** |
| `UPSTREAM_ERROR` | 5xx, timeout | thử lại, backoff mũ |
| `PARSE_FAIL` | 200 nhưng không đọc ra dữ liệu | **báo động** — nguồn đã đổi cấu trúc |

`PARSE_FAIL` là loại quan trọng nhất: retry vô ích, và nếu gộp chung vào "lỗi" thì
dữ liệu rỗng sẽ trôi âm thầm sang AI cho tới khi có người phát hiện.

---

## Cấu trúc

```
app/
├── crawl/          khung chịu lỗi, KHÔNG biết gì về nguồn cụ thể
│   ├── engine.py         điều phối chuỗi tier, ghi crawl_attempts
│   ├── contracts.py      hợp đồng mọi adapter phải theo
│   ├── outcomes.py       phân loại kết quả + dấu hiệu bị chặn từng sàn
│   ├── http.py           lấy HTML bằng HTTP  ─┐ cùng hình dạng trả về,
│   ├── browser_fetch.py  lấy HTML bằng Chrome ─┘ adapter dùng cái nào cũng như nhau
│   ├── paged_search.py   vòng lặp nhiều trang + quy tắc EMPTY/PARSE_FAIL
│   ├── html_json.py      bóc object JSON nhúng trong JS (1688, Taobao)
│   ├── policy.py · pacing.py · budget.py · cache.py
│   └── normalize.py      tiền tệ, domain
├── sources/        mỗi nguồn MỘT gói, mỗi tier MỘT adapter
│   ├── registry.py       nguồn → chuỗi adapter (thứ tự khai = thứ tự fallback)
│   ├── amazon/           vendor.py (T1) · browser.py (T2) · normalize.py
│   ├── alibaba_1688/     vendor.py (T1) · browser.py (T2) · normalize.py
│   ├── taobao/           vendor.py (T1) · browser.py (T2) · normalize.py
│   ├── shopify/ · bol/ · reddit/
│   └── fake/             fixture để chứng minh engine chạy đúng, không gọi mạng
├── services/       client của dịch vụ bên ngoài (apify, aggregator, cloak_browser…)
├── models/         SQLAlchemy — thêm model mới nhớ khai vào __init__.py
├── crud/           upsert theo khoá tự nhiên, đọc phục vụ AI
├── routers/        FastAPI
├── schemas/        Pydantic
├── jobs/           JobStore (Redis) + job function của arq
├── providers/      key pool · proxy pool · fallback cascade (Google Trends)
└── worker.py       đăng ký job cho arq
```

**Thêm một nguồn mới cần đụng đúng 4 chỗ:** tạo gói trong `sources/`, khai vào
`registry.py`, thêm job trong `jobs/crawl_jobs.py` + `worker.py`, thêm endpoint trong
`routers/ecom.py`. Không phải sửa `engine.py` — đó là cả mục đích của hợp đồng adapter.

**Quy ước dữ liệu:**

- Giá lưu **integer minor unit + mã tiền tệ** (`1999` + `"EUR"`), không dùng float.
- Thực thể chuẩn hoá là **1 dòng = 1 sản phẩm**, upsert theo khoá tự nhiên — quét lại
  không nhân đôi dữ liệu, chỉ cập nhật `last_seen_at`.
- Điểm giá chỉ ghi khi giá **đổi** — "hôm nay vẫn còn bán" đã nằm ở `last_seen_at`.
- Payload thô luôn lưu vào `api_audit_logs` để dựng lại khi sửa parser, không phải
  crawl lại và tốn quota.

---

## Việc còn nợ

- [ ] Xác thực giữa các service — hiện **chưa endpoint nào yêu cầu auth**
- [ ] Bảng `crawl_attempts` để đo tỉ lệ thành công và chi phí theo nguồn
- [ ] Chính sách xoá / phân vùng theo tháng cho `api_audit_logs`
- [ ] Mirror ảnh sang object storage (AI cần URL ổn định để tính embedding)
- [ ] Chuyển repo về org `relipasoft`

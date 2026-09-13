# [F2] Crawl Engine — Thiết kế tầng thu thập dữ liệu

> **Mục tiêu:** đạt tỉ lệ thành công cao và **ổn định theo thời gian** khi lấy dữ liệu từ Amazon, 1688, Taobao, AliExpress, Bol.com — và đưa dữ liệu đó sang AI Service ở dạng dùng được ngay.
> **Phạm vi:** tầng thu thập của `crawl-ecomerce-`. Không đụng scoring (thuộc `levelup_be`), không đụng phân tích LLM (thuộc `levelup_ai`).
> **Quan hệ với tài liệu cũ:** bổ sung cho [`DEV-Design-AI-Ecom-Data-Crawling.md`](./DEV-Design-AI-Ecom-Data-Crawling.md) — doc đó nói *crawl cái gì*, doc này nói *crawl thế nào để không chết*.
> **Trạng thái:** đề xuất thiết kế, chờ review.

---

## 0. Bốn quyết định cốt lõi

Nếu chỉ đọc một mục, đọc mục này.

| # | Quyết định | Vì sao |
| --- | --- | --- |
| **D1** | **Với Amazon / 1688 / Taobao: mua dữ liệu, đừng tự scrape.** Tự scrape chỉ là tầng dự phòng cuối. | Ba trang này có anti-bot ở mức đầu tư hàng chục triệu đô. Tự scrape đạt được 70% trong tuần đầu rồi tụt dần — chi phí thật không phải tiền proxy mà là **thời gian dev đi sửa mỗi khi họ đổi** |
| **D2** | **Đơn vị xoay vòng là "identity", không phải proxy.** Proxy + cookie + fingerprint đi theo cụm, sống chết cùng nhau. | Đổi IP mà giữ nguyên cookie, hoặc giữ IP mà đổi User-Agent, là **tín hiệu bot rõ hơn cả việc không đổi gì** |
| **D3** | **Phân loại kết quả trước, rồi mới quyết định retry.** Không gói mọi thứ vào `except`. | Retry một request bị captcha = đốt thêm identity. Retry một request timeout = hợp lý. Phân biệt được hai cái này là đòn bẩy lớn nhất lên tỉ lệ thành công |
| **D4** | **Mọi tham số chống chặn nằm trong DB, không nằm trong code.** | Khi Amazon siết, bạn cần hạ concurrency lúc 2 giờ sáng — bằng một câu `UPDATE`, không phải một lần deploy |

---

## 1. Vì sao base hiện tại chưa đủ

Base đã có đúng *ý tưởng* (key pool, proxy pool, cooldown, audit log) nhưng chưa đủ *độ sâu* cho nhóm trang khó. Bốn điểm cụ thể:

**1.1 — Pool là state trong process, mà hệ thống chạy nhiều process**

```python
# app/providers/proxy_pool.py
class ProxyPool:
    def __init__(self):
        self._lock = asyncio.Lock()   # lock trong 1 process
        self._index = 0               # con trỏ trong 1 process
```

`docker-compose.yml` chạy `api` và `worker` là **hai container riêng**. Hai process có hai `_index` độc lập → cùng lúc phát ra cùng một proxy. Khi scale worker lên 3 replica thì 3 process cùng đẩy tải vào một IP.

Thêm nữa, `_index % len(proxies)` chạy trên danh sách **đã lọc cooldown** — danh sách co giãn liên tục nên phép chia dư trỏ lung tung, không còn là round-robin. Có proxy bị gọi dồn, có proxy không bao giờ được dùng.

**1.2 — Chỉ biết "lỗi mạng", không biết "bị chặn"**

```python
# app/providers/proxy_pool.py — chỉ có một loại thất bại
async def mark_failed(self, db, proxy_id): ...   # cooldown cứng 5 phút
```

Amazon trả **HTTP 200 kèm trang captcha**. 1688 trả **302 sang trang `punish`**. Cả hai đều không phải "lỗi mạng" → proxy đã cháy vẫn được đánh dấu tốt và tiếp tục được phát ra.

**1.3 — Proxy và key rời nhau, không có cookie, không có fingerprint**

`ProxyPool` và `InMemoryKeyPool` độc lập hoàn toàn. Không có nơi nào lưu cookie jar hay dấu vân tay trình duyệt. Với trang chỉ đọc như Google Trends thì đủ; với Amazon/Taobao thì thiếu hẳn một chiều.

**1.4 — Không đo được**

`api_audit_logs` ghi `status` = `success | error`. Không trả lời được câu hỏi vận hành quan trọng nhất: *"tỉ lệ thành công của Amazon hôm nay so với tuần trước là bao nhiêu, và tụt vì tier nào?"*

**1.5 — `ProviderManager` là cascade viết cứng cho Google Trends**

```python
# app/providers/manager.py
async def search(self, params, db):
    try: return await self._try_serpapi(params)
    except AllKeysExhausted: pass
    try: return await self._try_direct_google(params, proxy_url=None)
    except GoogleBlockedError: pass
    return await self._try_direct_with_proxies(params, db)
```

Ý tưởng cascade **đúng** và nên giữ. Nhưng nó đang là một hàm riêng cho một nguồn. Thêm 5 nguồn nữa theo cách này là 5 bản sao của cùng một logic retry.

---

## 2. Nguyên tắc 1 — Tầng thu thập (Acquisition Tier)

Mỗi nguồn có một **chuỗi tầng xếp theo thứ tự ưu tiên**. Engine đi từ trên xuống, dừng ở tầng đầu tiên thành công.

```
T0  Official API      — nhà cung cấp cho phép chính thức. Ổn định nhất, hạn chế field nhất
T1  Vendor API        — bên thứ 3 đã giải bài toán anti-bot và bán lại. Đắt, nhưng họ chịu rủi ro
T2  Own browser       — Playwright + identity. Rẻ nhất theo request, đắt nhất theo công bảo trì
T3  Cache / degraded  — trả dữ liệu cũ kèm nhãn "stale", thay vì trả lỗi
```

### Chuỗi tầng đề xuất theo nguồn

| Nguồn | T0 Official | T1 Vendor | T2 Tự scrape | Khuyến nghị |
| --- | --- | --- | --- | --- |
| **Amazon** | PA-API 5.0 — thiếu sales volume & review đầy đủ, **yêu cầu tài khoản Associate có phát sinh đơn** để giữ quyền | Rainforest · Oxylabs E-Comm · Bright Data · Apify actor | Rất khó: captcha ảnh, phát hiện headless, chặn theo IP range | **T1 làm chính.** T0 chỉ bù field giá/ảnh nếu xin được |
| **1688** | Alibaba Open Platform — **cần pháp nhân + xác thực thực danh tại TQ**, thực tế khó với công ty VN | Aggregator TQ (onebound/万邦 và tương đương) — có sẵn `item_search_img` cho tìm bằng ảnh | Rất khó: bắt đăng nhập, slider captcha, `x5sec` device token | **T1 làm chính.** Đây là nguồn nên mua dứt khoát nhất |
| **Taobao** | Như 1688 | Như 1688 | Khó nhất trong nhóm | **T1 làm chính** |
| **AliExpress** | Open Platform / Dropshipper API — **dễ xin hơn hẳn Taobao**, đây là cửa sáng nhất của nhóm Alibaba | Apify actor | Trung bình | **T0 làm chính** — đáng ưu tiên xin sớm |
| **Bol.com** | Retailer API chỉ cho seller của chính mình | Ít vendor phủ | Anti-bot nhẹ → **khả thi** | **T2 chấp nhận được** |
| **Shopify store đối thủ** | `/products.json` công khai, miễn phí, chính xác | — | — | **T0**, không cần gì thêm |

> **Việc cần làm trước khi code T1:** khảo sát giá thật theo quy mô mục tiêu cho từng vendor, và **chạy thử 100 request thật** đo tỉ lệ thành công trước khi ký hợp đồng năm. Bảng khảo sát dùng lại đúng bộ cột của `Tool_Integration_Research_Requirement_DEV.md`.

### 2.1. AliExpress và Bol.com — nhóm "dễ", nhưng dễ theo hai kiểu khác nhau

Amazon/1688/Taobao khó vì anti-bot. Hai nguồn này không khó vì anti-bot, mà mỗi cái vướng một chỗ riêng — nên chiến lược cũng khác.

#### AliExpress — nguồn T0 nên xin sớm nhất

Đây là **nguồn duy nhất xuất hiện ở cả hai vế của SRS**:

| Vế | Vai trò |
| --- | --- |
| Bước 2 (F2.3) | Sàn TMĐT để tìm sản phẩm win — *"Amazon, AliExpress, eBay, Etsy, Bol.com"* |
| Bước 4 (F2.5) | **Nguồn hàng** — *"1688, Taobao, Alibaba, Express, Kho EU"* |

→ **AliExpress giảm rủi ro cho F2.5.** Nếu 1688/Taobao bế tắc (proxy TQ đắt, không ký được aggregator), AliExpress vẫn cho một mức giá vốn tham chiếu đủ để kiểm tra `COGS_max` ở Bước 3 — trong khi giá đàm phán thật thì Bước 4 vốn đã do đội mua hàng nhập tay.

Đường vào API — chọn đúng chương trình mới quan trọng:

| Chương trình | Cho gì | Điều kiện |
| --- | --- | --- |
| Affiliate API (`aliexpress.affiliate.*`) | Search, chi tiết, hot products, danh mục | Tài khoản affiliate được duyệt. ⚠️ Có thể chỉ phủ sản phẩm đủ điều kiện affiliate, **không phải toàn sàn** — cần xác minh |
| Dropshipping API (`aliexpress.ds.*`) | Chi tiết, cước vận chuyển, đặt đơn | Phải ở trong chương trình dropship |

Xác thực là **App Key + Secret kèm chữ ký request**, không phải bearer token — dự trù thời gian cho việc implement đúng thuật toán ký.

**Điểm sáng:** AliExpress công khai **số đơn đã bán** — xem mục 2.2, đây là nguồn duy nhất trong ba nguồn ecom có trường này.

#### Bol.com — ngược hoàn toàn với Amazon

**Quan trọng nhất cho thị trường target chính.** SRS liệt kê Hà Lan `NL` đầu tiên; ở NL/BE thì Bol.com là sàn thống trị, không phải Amazon:

```
Amazon   → DE · FR · IT · ES
Bol.com  → NL · BE
```

Hai nguồn phủ đúng 5 thị trường SRS nhắm tới. **Bỏ Bol.com là mất thị trường được ưu tiên số một.**

Nhưng không mua được:

- **Retailer API** chỉ thấy đơn/offer của chính mình → vô dụng cho nghiên cứu đối thủ
- **Catalog/search API công khai** từng có, đã bị siết/ngừng — ⚠️ cần xác minh trạng thái hiện tại trước khi lên kế hoạch
- Vendor scraping tập trung vào Amazon, **độ phủ Bol.com mỏng hoặc không có**

→ **Bol.com là nơi duy nhất mà tự scrape (T2) vừa cần thiết vừa hợp lý** — đúng ngược với Amazon. Anti-bot nhẹ hơn hẳn, datacenter proxy đủ dùng, concurrency cao hơn được.

Bù lại, Bol.com cho một thứ Amazon giấu: **bảng bestseller theo danh mục** — đúng cái *"Top lượt mua"* mà SRS Bước 2 yêu cầu, lấy trực tiếp thay vì phải suy đoán.

### 2.2. Vấn đề xuyên suốt: hai trong ba nguồn ecom không có sales volume

SRS chấm `S2.2` dựa trên **`Sales Volume` + `Average Rating`**. Thực tế:

| Nguồn | Sales volume | Chỉ số thay thế |
| --- | --- | --- |
| **AliExpress** | ✅ công khai số đơn | — |
| **Amazon** | ❌ không công bố | BSR trong danh mục · số review · tốc độ tăng review |
| **Bol.com** | ❌ không công bố | Thứ hạng bestseller danh mục · số review |

Nếu code theo đúng chữ SRS, `S2.2` của sản phẩm Amazon/Bol.com sẽ luôn `0` hoặc `N/A` — và sản phẩm AliExpress tự nhiên xếp hạng cao hơn **chỉ vì nó là nguồn duy nhất có đủ trường dữ liệu**, không phải vì nó tốt hơn.

**Cần chốt với BA:** hoặc thay `Sales Volume` bằng một chỉ số proxy chuẩn hoá được giữa các sàn, hoặc bỏ nó khỏi `S2.2` và chấm bằng rating + review count. Đây là lỗi lệch hạng có hệ thống, không phải thiếu sót nhỏ.

### Vì sao xếp tầng lại làm tăng tỉ lệ thành công

Không phải vì tầng nào giỏi hơn, mà vì **thất bại của các tầng không tương quan với nhau**. Vendor chết vì hết credit; self-scrape chết vì IP bị chặn. Hai nguyên nhân độc lập → xác suất cả hai cùng chết thấp hơn nhiều so với từng cái.

Điều kiện để điều này đúng: **fallback phải tự động**, và tầng dưới phải trả **đúng một schema** với tầng trên. Đó là lý do có mục 7.2.

---

## 3. Nguyên tắc 2 — Identity thay cho proxy rời

### Vấn đề

Anti-bot hiện đại không chấm điểm từng request, nó chấm điểm **một danh tính qua thời gian**. Các tín hiệu được gộp lại: IP, cookie, TLS fingerprint (JA3/JA4), HTTP/2 frame order, User-Agent, độ phân giải màn hình, timezone, ngôn ngữ, WebGL renderer, nhịp request.

Một danh tính "sạch" là danh tính mà các tín hiệu đó **nhất quán với nhau** và **thay đổi chậm**. Rotate proxy mỗi request trong khi giữ nguyên cookie tạo ra một người dùng nhảy từ Đức sang Brazil trong 3 giây — tín hiệu bot rõ hơn cả việc dùng một IP cố định.

### Thiết kế

Gói tất cả vào một thực thể có vòng đời:

```python
@dataclass
class Identity:
    id: int
    proxy_url: str                  # nên là residential/mobile cho Amazon & Alibaba
    fingerprint: dict               # UA, viewport, locale, timezone, platform, webgl
    cookie_jar: dict                # tuần tự hoá được, gắn theo domain
    account_ref: str | None         # với 1688/Taobao khi phải đăng nhập
    home_country: str               # phải khớp geo của proxy
    state: Literal["ready", "leased", "cooling", "quarantined", "burned"]
    health: float                   # 0..1, EWMA theo kết quả gần đây
```

**Quy tắc bất biến:**

1. Một identity **gắn với một quốc gia**. Proxy Đức thì `Accept-Language: de-DE`, timezone `Europe/Berlin`. Lệch là cờ đỏ.
2. Cookie chỉ sống cùng proxy đã sinh ra nó. Proxy chết → cookie vứt theo.
3. Fingerprint **cố định trong suốt đời identity**, không random mỗi request.
4. Identity có **tuổi thọ**: hết `max_requests` hoặc `max_age` thì nghỉ hưu êm, không đợi đến khi bị chặn.
5. Lease là **độc quyền** — một identity chỉ phục vụ một job tại một thời điểm, để nhịp request không bị chồng.

### Lease / release qua Redis, không qua asyncio.Lock

Phải cross-process vì `api` và `worker` là hai container:

```python
# app/crawl/identity.py
async def lease(source: str, country: str, ttl_s: int) -> Identity | None:
    """Lấy identity khoẻ nhất đang rảnh. Dùng Lua script để atomic
    trên nhiều worker — thay cho asyncio.Lock hiện tại (chỉ đúng trong 1 process)."""

async def release(identity_id: int, outcome: Outcome) -> None:
    """Trả identity về pool và cập nhật sức khoẻ theo BẢNG mục 4,
    không phải cooldown cứng 5 phút như hiện tại."""
```

`health` cập nhật kiểu EWMA — một lần fail không giết identity, nhưng chuỗi fail thì giảm nhanh. Khi `health < ngưỡng` → `quarantined`.

### Về proxy: loại nào cho nguồn nào

| Nguồn | Loại proxy | Ghi chú |
| --- | --- | --- |
| Amazon | **Residential**, sticky session 5–10 phút, geo khớp marketplace | Datacenter IP bị chặn gần như ngay |
| 1688 / Taobao | **Residential Trung Quốc đại lục** | Đây là ràng buộc thật: proxy ngoài TQ bị giới hạn/chuyển hướng. Nếu đi tầng T1 thì vendor lo phần này — thêm một lý do chọn T1 |
| Bol.com | Datacenter là đủ | Anti-bot nhẹ |
| Shopify `/products.json` | Không cần proxy | Công khai, chỉ cần throttle |

---

## 4. Nguyên tắc 3 — Phân loại kết quả

Đây là phần đòn bẩy lớn nhất, và là phần base hiện chưa có.

### Bảng phân loại

| Outcome | Nhận biết | Hành động | Xử lý identity |
| --- | --- | --- | --- |
| `OK` | Parse ra dữ liệu hợp lệ | Lưu, kết thúc | `health` ↑ |
| `EMPTY` | 200, parse được, **0 kết quả** | **Không retry.** Đây là câu trả lời hợp lệ | không đổi |
| `RATE_LIMITED` | 429, hoặc 430 (Shopify), có `Retry-After` | Backoff **đúng identity đó**, tôn trọng `Retry-After` | `cooling` theo header |
| `SOFT_BLOCK` | Captcha, challenge, redirect `punish`/`login` | **Đổi identity rồi mới retry.** Retry cùng identity là vô nghĩa | `quarantined` 30–120 phút |
| `HARD_BLOCK` | IP bị cấm, account bị khoá | Đổi identity, **báo động** nếu tỉ lệ vượt ngưỡng | `burned` — bỏ vĩnh viễn |
| `UPSTREAM_ERROR` | 5xx, timeout, reset kết nối | Retry **cùng identity**, backoff mũ | `health` ↓ nhẹ |
| `PARSE_FAIL` | 200, không có dấu hiệu chặn, nhưng selector không khớp | **Không retry — báo động ngay.** Trang đã đổi layout | không đổi |
| `QUOTA_EXHAUSTED` | Vendor báo hết credit | Nhảy sang tầng kế tiếp | n/a |

> **`PARSE_FAIL` là loại quan trọng nhất mà hầu hết crawler bỏ qua.** Nó không phải lỗi mạng, retry bao nhiêu lần cũng không khỏi, và nếu gộp chung vào `error` thì đội dev chỉ phát hiện khi AI Service đã ăn hàng nghìn bản ghi rỗng. Phải là một kênh alert riêng.

### Bộ nhận biết theo nguồn

```python
# app/crawl/classify.py
AMAZON_SOFT = (
    "/errors/validateCaptcha",
    "Enter the characters you see below",
    "Sorry, we just need to make sure you're not a robot",
)
ALIBABA_SOFT = ("login.taobao.com", "x5secdata", "_____tmd_____", "/punish")
CLOUDFLARE   = ("__cf_chl", "cf-mitigated", "Just a moment")
```

Quan trọng: **kiểm tra body, không chỉ status code**. Amazon trả `200 OK` kèm trang captcha — nếu chỉ nhìn status thì đó là "thành công" và bạn lưu HTML rác vào DB.

Ngoài ra có một **canary rẻ tiền** đáng làm: mỗi nguồn có một URL đã biết chắc kết quả (ví dụ một ASIN cố định). Chạy 15 phút/lần. Canary fail trước khi job thật fail → biết sớm là nguồn đang siết, hạ concurrency chủ động thay vì đốt identity.

---

## 5. Nguyên tắc 4 — Policy theo domain nằm trong DB

```
crawl_source_policies
  source                 -- 'amazon' | 'alibaba_1688' | 'taobao' | 'aliexpress' | 'bol' | 'shopify'
  tier_chain             -- jsonb: ["vendor_rainforest", "browser"]  (thứ tự fallback)
  max_concurrency        -- số request đồng thời tối đa TOÀN HỆ THỐNG
  min_delay_ms           -- khoảng cách tối thiểu giữa 2 request cùng identity
  jitter_ms              -- ngẫu nhiên cộng thêm, tránh nhịp đều như máy
  max_attempts
  identity_max_requests  -- nghỉ hưu identity sau bao nhiêu request
  identity_max_age_s
  soft_block_cooldown_s
  respect_retry_after    -- bool
  daily_budget_usd       -- trần chi phí, vượt thì dừng nguồn đó
  enabled
```

**Concurrency và pacing phải cưỡng chế qua Redis**, không qua semaphore trong process — cùng lý do mục 1.1.

```python
# app/crawl/pacing.py
async def acquire_slot(source: str, policy: Policy) -> AsyncContextManager:
    """Semaphore phân tán trên Redis, tự hết hạn (tránh kẹt khi worker chết đột ngột)."""

async def wait_turn(identity_id: int, source: str, policy: Policy) -> None:
    """Ngủ cho đủ min_delay + jitter tính từ lần request trước của CHÍNH identity này."""
```

Giá trị khởi đầu đề xuất — **cố tình thận trọng**, nới dần khi số liệu cho phép:

| Nguồn | Tier chính | concurrency | min_delay | identity_max_requests |
| --- | --- | --- | --- | --- |
| Amazon | T1 vendor | 2 | 4000 ms | 40 |
| 1688 / Taobao | T1 vendor | 1 | 6000 ms | 25 |
| **AliExpress** | **T0 API** | theo quota app | — | không dùng identity |
| **Bol.com** | **T2 tự scrape** | 4 | 1500 ms | 150 |
| Shopify `/products.json` | T0 công khai | 2 | 1500 ms | — |

Hai dòng in đậm là hai nguồn **không đi theo mặc định T1**: AliExpress vì có API chính thức tốt hơn mọi vendor, Bol.com vì không vendor nào phủ. Xem mục 2.1.

> Bắt đầu chậm rồi nới ra **rẻ hơn nhiều** so với bắt đầu nhanh rồi bị chặn — vì lệnh cấm thường theo dải IP, mất cả pool chứ không mất một IP.

---

## 6. Nguyên tắc 5 — Đo tỉ lệ thành công

Không có bảng này thì mọi tinh chỉnh ở trên chỉ là cảm tính.

```
crawl_attempts
  id, request_id            -- gộp các attempt của cùng một FetchRequest
  source, capability        -- 'search_keyword' | 'search_image' | 'detail' | 'reviews'
  tier                      -- 'official' | 'vendor:<name>' | 'browser'
  identity_id               -- null với tier API
  attempt_no
  outcome                   -- theo enum mục 4
  http_status, latency_ms
  cost_usd                  -- credit vendor / ước tính proxy
  error_detail
  created_at
```

Từ một bảng này trả lời được:

- Tỉ lệ thành công theo `(source, tier, ngày)` — **chỉ số vận hành chính**
- Chi phí thật cho một lần research (ghép với `run_id` phía `levelup_be`)
- Identity nào đang kéo tỉ lệ xuống
- `PARSE_FAIL` tăng đột biến = trang đổi layout, cần sửa selector

**Alert tối thiểu:** tỉ lệ `OK` của một nguồn tụt dưới ngưỡng trong 30 phút · `PARSE_FAIL` > 5 lần liên tiếp · chi tiêu ngày chạm 80% trần.

---

## 7. Kiến trúc code

### 7.1. Bố cục thư mục

Giữ nguyên convention hiện có (`router → service → crud → model`), thêm gói `crawl/` làm khung dùng chung:

```
app/
├── crawl/                     [MỚI] khung chịu lỗi, không biết gì về nguồn cụ thể
│   ├── contracts.py           FetchRequest / FetchResult / Outcome / Capability
│   ├── engine.py              pipeline 13 bước, fallback theo tier
│   ├── identity.py            lease/release qua Redis Lua
│   ├── pacing.py              semaphore + nhịp phân tán
│   ├── classify.py            phân loại outcome theo nguồn
│   ├── policy.py              đọc + cache crawl_source_policies
│   └── budget.py              trần chi phí
│
├── sources/                   [MỚI] mỗi nguồn một gói, mỗi tier một adapter
│   ├── registry.py            source → chuỗi adapter
│   ├── amazon/
│   │   ├── vendor.py          T1
│   │   ├── browser.py         T2 — Playwright
│   │   └── normalize.py       → NormalizedEcomProduct
│   ├── alibaba_1688/
│   │   ├── vendor.py          T1 — gồm cả tìm bằng ảnh
│   │   ├── browser.py         T2
│   │   └── normalize.py       → NormalizedSupplierListing
│   ├── taobao/ · aliexpress/ · bol/ · shopify/
│   └── reddit/                F2.6 — bổ sung theo SRS v1.1
│
├── browser/                   [MỚI] chỉ dùng khi chạy T2
│   ├── pool.py                pool context Playwright, tái dùng, tự hồi sinh
│   └── stealth.py             áp fingerprint của identity lên context
│
├── media/                     [MỚI] mirror ảnh sang object storage
│   └── mirror.py
│
├── providers/                 giữ nguyên — key pool, proxy pool (nâng cấp thành identity)
├── services/                  giữ nguyên — apify_client, serpapi_client, openrouter_client
├── routers/ · models/ · crud/ theo convention cũ
└── worker.py                  thêm queue riêng cho job chạy browser
```

### 7.2. Hợp đồng adapter

Toàn bộ giá trị của thiết kế nằm ở chỗ **mọi tier trả về cùng một kiểu**. Có vậy fallback mới trong suốt với tầng trên.

```python
# app/crawl/contracts.py
class Capability(StrEnum):
    SEARCH_KEYWORD = "search_keyword"
    SEARCH_IMAGE   = "search_image"     # bắt buộc cho 1688 — SRS Bước 4
    DETAIL         = "detail"
    REVIEWS        = "reviews"

@dataclass(frozen=True)
class FetchRequest:
    source: str
    capability: Capability
    params: dict                  # đã chuẩn hoá, dùng luôn làm khoá cache
    country: str
    idempotency_key: str          # hash(source, capability, params) — xem 9.3
    run_id: str | None = None     # để quy chi phí về một lần research

@dataclass
class FetchResult:
    outcome: Outcome
    items: list[dict]             # ĐÃ chuẩn hoá — không phải payload thô của vendor
    raw_ref: str | None           # trỏ sang api_audit_logs để replay
    tier_used: str
    cost_usd: float
    stale: bool = False           # True khi rơi xuống T3 cache

class SourceAdapter(Protocol):
    tier: str
    capabilities: set[Capability]
    needs_identity: bool
    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult: ...
```

> **Ràng buộc bắt buộc:** adapter **phải** trả `items` đã chuẩn hoá. Nếu để payload thô của Rainforest rò rỉ lên trên, thì ngày đổi sang Oxylabs sẽ phải sửa cả tầng nghiệp vụ — và fallback T1→T2 sẽ không bao giờ trong suốt được. Payload thô vẫn lưu đầy đủ ở `api_audit_logs`, chỉ là không ai đọc nó trong luồng chính.

### 7.3. Pipeline

```python
# app/crawl/engine.py
async def fetch(req: FetchRequest) -> FetchResult:
    policy = await policy_for(req.source)                    # 1. đọc policy (có cache)
    if hit := await cache_lookup(req.idempotency_key, policy.cache_ttl):
        return hit                                           # 2. cache
    await budget.guard(req.source, policy)                   # 3. trần chi phí

    last: FetchResult | None = None
    for adapter in registry.chain(req.source, req.capability, policy):   # 4. theo tier
        for attempt in range(policy.max_attempts):
            async with pacing.acquire_slot(req.source, policy):          # 5. concurrency
                identity = await identity.lease(...) if adapter.needs_identity else None
                if adapter.needs_identity and identity is None:
                    break                                    # hết identity → nhảy tier
                try:
                    await pacing.wait_turn(identity, req.source, policy) # 7. nhịp
                    result = await adapter.fetch(req, identity)          # 8. gọi
                finally:
                    outcome = classify(result, req.source)               # 9. phân loại
                    await record_attempt(req, adapter, identity, outcome)# 10. đo
                    if identity:
                        await identity_release(identity, outcome)        # 11. sức khoẻ

            last = result
            if outcome is Outcome.OK:
                await persist(req, result)                   # 12. raw + normalized
                await enqueue_mirror_images(result)          # 13. ảnh (job phụ)
                return result
            if not should_retry(outcome, policy):            # EMPTY/PARSE_FAIL → dừng
                break

    return await serve_stale_or_fail(req, last)              # T3
```

Điểm đáng chú ý: **`classify` nằm trong `finally`**, nên cả trường hợp exception lẫn trường hợp trả 200-kèm-captcha đều đi qua cùng một cửa. Đây chính là chỗ base hiện tại để lọt — `except ApifyError` bắt được lỗi mạng nhưng không bắt được trang captcha.

### 7.4. Playwright — chỉ khi thật sự cần

Chromium ngốn ~300–500 MB mỗi context. **Không chạy chung process với `api`.** Thêm một container thứ ba:

```yaml
# docker-compose.yml
worker-browser:
  command: arq app.worker_browser.WorkerSettings
  shm_size: 1gb          # thiếu cái này Chromium crash ngẫu nhiên, rất khó debug
  deploy:
    resources: { limits: { memory: 2g } }
```

Queue tách riêng (`crawl-browser`) để job Playwright không chiếm chỗ của job API nhẹ.

Trong `browser/stealth.py`, fingerprint của identity được áp lên context — cùng một identity thì luôn cùng UA, viewport, locale, timezone:

```python
ctx = await browser.new_context(
    proxy={"server": identity.proxy_url},
    user_agent=identity.fingerprint["ua"],
    viewport=identity.fingerprint["viewport"],
    locale=identity.fingerprint["locale"],
    timezone_id=identity.fingerprint["tz"],
    storage_state=identity.cookie_jar,
)
```

Sau mỗi phiên, **ghi cookie jar trở lại identity**. Đây là điểm khác biệt so với base hiện tại: session được tích luỹ chứ không vứt đi.

---

## 8. Data model bổ sung

### 8.1. Bảng hạ tầng crawl

| Bảng | Nội dung |
| --- | --- |
| `crawl_identities` | proxy · fingerprint jsonb · cookie_jar jsonb · country · state · health · used_count · last_used_at · quarantined_until |
| `crawl_source_policies` | theo mục 5 |
| `crawl_attempts` | theo mục 6 |
| `crawl_cache` | idempotency_key (unique) · source · capability · payload jsonb · fetched_at · expires_at |

### 8.2. Bảng thực thể đã chuẩn hoá

Đây là thứ AI Service đọc. **Một dòng một thực thể** — không nhét cả mảng vào một ô JSONB như `facebook_ads_results` hiện nay:

| Bảng | Khoá tự nhiên | Trường chính |
| --- | --- | --- |
| `ecom_products` | `(source, external_id)` unique | title · price_minor + currency · rating · review_count · sales_volume · url · image_refs[] · seller · `first_seen_at` · `last_seen_at` |
| `ecom_reviews` | `(source, external_review_id)` | product_id · rating · text · lang · posted_at |
| `supplier_listings` | `(source, external_id)` unique | supplier_name · listed_cogs_minor · moq · years_active · rating · url · image_refs[] |
| `ad_signals` | `(source, external_ad_id)` unique | active_days · reach · ad_copy · media_refs[] · landing_page_url |
| `price_points` | — | source_type · url · listed_price_minor · currency · **fx_rate + fx_at** · combo_qty · unit_price_minor |
| `reddit_threads` / `reddit_comments` | `(source, external_id)` | subreddit · score · text · posted_at — cho F2.6 |

**Ba quy ước bắt buộc:**

1. **Tiền tệ lưu integer minor unit + mã tiền tệ.** Không dùng float. Mọi giá quy đổi lưu kèm `fx_rate` và `fx_at` để tái lập được — cùng quy ước với `levelup_be`.
2. **`first_seen_at` / `last_seen_at` thay cho việc chèn trùng.** Upsert theo khoá tự nhiên. Cùng sản phẩm gặp lại ở lần crawl sau chỉ cập nhật `last_seen_at` — nhờ đó tự có luôn lịch sử giá mà không cần bảng riêng.
3. **Giữ `external_id` gốc.** Đây là thứ AI SKU Matching dùng để gắn `candidate_id` sau khi gộp cụm.

---

## 9. Hợp đồng phục vụ AI Service

### 9.1. Ảnh phải được mirror — đây là yêu cầu, không phải tối ưu

AI Service cần ảnh để tính CLIP embedding (SRS Bước 2.2) và để tìm nguồn hàng bằng ảnh (SRS Bước 4.1). Không thể đưa URL gốc của sàn vì:

- CDN của Alibaba/Taobao **chặn hotlink** tuỳ referer, và URL có thể hết hạn
- Gọi lại nguồn mỗi lần tính embedding là tự bắn vào rate-limit của chính mình
- Ảnh biến mất khi listing bị gỡ → mất khả năng tái lập kết quả cũ

Thiết kế: job phụ `mirror_images` tải ảnh ngay khi crawl xong, đẩy lên **cùng bucket R2/S3 mà `levelup_be` đang dùng** (không dựng hạ tầng lưu trữ mới), lưu key vào `image_refs[]`. AI chỉ đọc URL nội bộ.

### 9.2. Hai kiểu truy cập

```
POST /api/v1/crawl/jobs          → 202 {job_id}     đặt hàng một lần crawl
GET  /api/v1/crawl/jobs/{id}                        poll trạng thái
POST callback_url (tuỳ chọn)                        gọi ngược khi xong, đỡ phải poll

GET  /api/v1/ecom/products?source=&updated_since=&cursor=    đọc hàng loạt để matching
GET  /api/v1/suppliers?...
GET  /api/v1/ad-signals?...
```

`updated_since` + cursor cho phép AI Service **đọc tăng dần** thay vì quét lại toàn bảng mỗi lần chạy matching.

### 9.3. Idempotency — để AI hỏi lại mà không tốn tiền

`idempotency_key = sha256(source | capability | canonical(params))`.

Gửi lại cùng request trong thời gian `cache_ttl` → trả kết quả cũ, `cost_usd = 0`. AI Service được phép retry thoải mái mà không đốt credit vendor. `cache_ttl` để trong policy, khác nhau theo nguồn: giá đối thủ nên ngắn (vài giờ), listing nguồn hàng có thể dài hơn (vài ngày).

### 9.4. Xác thực giữa các service

Hiện **chưa có auth trên bất kỳ endpoint nào**. Tối thiểu: shared API key qua header `X-Internal-Key`, validate bằng FastAPI dependency toàn cục, key nằm trong `.env`. Thêm `X-Run-Id` để quy chi phí về đúng lần research bên `levelup_be`.

---

## 10. Tìm bằng ảnh trên 1688 (SRS Bước 4.1)

Đây là yêu cầu cốt lõi của Bước 4 và là phần khó nhất, nên tách riêng:

```
Ảnh sản phẩm (từ ecom_products.image_refs)
  → mirror sang R2 → URL công khai ổn định
  → Adapter alibaba_1688 · Capability.SEARCH_IMAGE
      T1: vendor API dạng item_search_img (nhận URL ảnh hoặc bytes)
      T2: Playwright — mở trang tìm ảnh, upload file, đọc kết quả  ⚠️ rất dễ vỡ
  → normalize → supplier_listings
```

**Khuyến nghị: chỉ làm T1 cho capability này.** Tự động hoá luồng upload ảnh trên UI 1688 vừa phải vượt captcha vừa phụ thuộc DOM — chi phí bảo trì không tương xứng, trong khi SRS Bước 4 **vốn đã có con người** (đội mua hàng) đàm phán và nhập giá thật. Crawl ở đây chỉ để gợi ý sơ bộ.

---

## 11. Thứ tự triển khai

Xếp theo nguyên tắc: **thứ không phụ thuộc nhà cung cấp làm trước**, thứ chờ duyệt thì nộp hồ sơ ngay từ ngày đầu.

| Giai đoạn | Nội dung | Kết quả kiểm chứng được |
| --- | --- | --- |
| **G0** — ngày 1, không cần code | Nộp hồ sơ AliExpress Open Platform · dùng thử Rainforest/Oxylabs · hỏi giá aggregator TQ · xác nhận với Lead/Legal về rủi ro ToS khi tự scrape | Có sandbox key để thử |
| **G1** — nền, không phụ thuộc ai | Alembic thật (bỏ `create_all`) · `crawl/contracts.py` · `engine.py` · `classify.py` · policy trong DB · `crawl_attempts` · adapter **fake đọc fixture** | Chạy hết pipeline bằng fake, **có số liệu tỉ lệ thành công giả lập** |
| **G2** — identity | Nâng `ProxyPool` → `IdentityPool` trên Redis · fingerprint · cookie jar · lease/release theo outcome | 2 worker container chạy song song không phát trùng identity |
| **G3** — nguồn dễ trước | Shopify `/products.json` (T0, free) → **Bol.com** (T2) → **AliExpress** (T0, nếu quyền đã về) | Data thật đầu tiên, rủi ro thấp nhất, kiểm chứng khung. Bol.com phủ đúng thị trường ưu tiên số 1 (NL) |
| **G4** — nguồn đắt | Amazon T1 vendor | Đo tỉ lệ thành công thật trước khi cam kết hợp đồng năm |
| **G5** — nguồn khó | 1688 / Taobao T1 · tìm bằng ảnh | |
| **G6** — vận hành | Mirror ảnh · canary · alert · trần chi phí · retention cho `api_audit_logs` | |
| **G7** | Reddit VOC (F2.6) · Playwright T2 cho Amazon **nếu** vẫn cần | |

**Cột mốc đáng nhắm ở G1:** chạy hết pipeline bằng fake adapter, có bảng `crawl_attempts` sinh số liệu, có fallback tier hoạt động — demo được toàn bộ cơ chế chịu lỗi **trước khi** tiêu một đồng nào cho vendor.

---

## 12. Rủi ro & điều chưa chốt

| Vấn đề | Mức | Ghi chú |
| --- | --- | --- |
| **Pháp lý / ToS** | 🔴 | Tự scrape Amazon, Alibaba, 1688 đều vi phạm điều khoản của họ. Đi tầng T1 **chuyển rủi ro sang vendor** — đây là một lý do chọn T1 ngang với lý do kỹ thuật. Cần Lead/Legal biết và chấp nhận trước khi code T2 |
| **Chi phí vendor vượt dự toán** | 🟡 | `daily_budget_usd` + `crawl_attempts.cost_usd` + cache theo idempotency từ ngày đầu. Chưa chốt: "10.000 product scans/tháng" nghĩa là 1 run hay 1 candidate? |
| **Proxy residential Trung Quốc** | 🟡 | Ràng buộc thật cho 1688/Taobao ở T2, giá cao và nguồn cung hạn chế. Nếu không giải được thì T2 cho hai nguồn này coi như không khả thi — càng củng cố D1 |
| **`PARSE_FAIL` âm thầm** | 🟡 | Trang đổi layout → dữ liệu rỗng trôi vào AI mà không ai biết. Đã có kênh alert riêng ở mục 4, nhưng cần người trực |
| **Vendor T1 cũng có thể chết** | 🟡 | Rainforest/Oxylabs cũng đứt dịch vụ. Chuỗi tier phải có **ít nhất 2 tầng thật** cho mỗi nguồn quan trọng, không chỉ 1 vendor + 1 cache |
| **Repo còn ở GitHub cá nhân** | 🟡 | Chuyển sang org `relipasoft` — đã ghi ở doc cũ, chưa làm |

### Câu hỏi cần chốt trước khi code G2

1. **Ngân sách vendor T1 là bao nhiêu/tháng?** Câu trả lời quyết định T1 là đường chính hay chỉ là dự phòng — và do đó quyết định có cần đầu tư sâu vào Playwright T2 hay không.
2. **Có chấp nhận rủi ro ToS cho T2 không?** Nếu không, bỏ hẳn nhánh browser khỏi Amazon/Alibaba và thiết kế gọn đi đáng kể.
3. **AI Service poll hay nhận callback?** Mục 9.2 đề xuất hỗ trợ cả hai, nhưng nên chốt một cái làm đường chính để khỏi phải bảo trì cả hai.
4. **DTO chuẩn hoá của crawler có khớp interface provider bên `levelup_be` không?** (`NormalizedEcomProduct`, `NormalizedSupplierListing`, `NormalizedAdSignal` ở `docs/plan/product-rnd-automation.md` §4.2). Nếu khớp thì crawler chính là implementation của các interface đó và **không phải chuẩn hoá hai lần ở hai repo**. Nên chốt ngay bây giờ, trước khi viết adapter đầu tiên.

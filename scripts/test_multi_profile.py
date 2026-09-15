"""
Test mở 5 profile CloakBrowser cùng lúc vào cùng 1 URL.

Usage:
    python scripts/test_multi_profile.py https://example.com
    python scripts/test_multi_profile.py https://bot.sannysoft.com --screenshot
    python scripts/test_multi_profile.py https://fingerprint.com/demo --headed
"""
import argparse
import asyncio
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings

PROFILES = 5
FINGERPRINT_SEEDS = [random.randint(1, 999_999_999) for _ in range(PROFILES)]

SCREEN_W, SCREEN_H = 1920, 1080


def calc_positions(monitor: int = 1) -> list[tuple[int, int, int, int]]:
    """
    Tính vị trí 5 cửa sổ theo monitor (1=trái x=0, 2=phải x=1920).
    Layout:
      Hàng trên: 3 cửa sổ đều nhau (640x540)
      Hàng dưới: 2 cửa sổ đều nhau (960x540)
    """
    ox = 0 if monitor == 1 else SCREEN_W  # x offset
    w3 = SCREEN_W // 3      # 640
    w2 = SCREEN_W // 2      # 960
    h2 = SCREEN_H // 2      # 540
    return [
        (ox + 0,       0,   w3, h2),   # Profile 1 — trên trái
        (ox + w3,      0,   w3, h2),   # Profile 2 — trên giữa
        (ox + w3 * 2,  0,   w3, h2),   # Profile 3 — trên phải
        (ox + 0,       h2,  w2, h2),   # Profile 4 — dưới trái
        (ox + w2,      h2,  w2, h2),   # Profile 5 — dưới phải
    ]


@dataclass
class ProfileResult:
    profile_idx: int
    fingerprint_seed: int
    status: str
    source: str = ""
    title: str | None = None
    page_text_preview: str | None = None
    screenshot_path: str | None = None
    latency_ms: int = 0
    error: str | None = None


# ── Path A: cloakserve qua CDP (async) ───────────────────────────────────────

async def run_via_cdp(idx: int, seed: int, url: str, headless: bool, take_screenshot: bool) -> ProfileResult:
    from playwright.async_api import async_playwright

    t0 = time.monotonic()
    cloak_url = settings.cloak_browser_url.rstrip("/")
    cdp_endpoint = f"{cloak_url}?fingerprint={seed}"

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(1)

        title = await page.title()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")

        screenshot_path: str | None = None
        if take_screenshot:
            path = f"/tmp/profile_{idx+1}_seed{seed}.png"
            await page.screenshot(path=path)
            screenshot_path = path

        await context.close()
        await browser.close()

    return ProfileResult(
        profile_idx=idx + 1, fingerprint_seed=seed, status="success",
        source="cloakserve-cdp", title=title,
        page_text_preview=text[:300].replace("\n", " ").strip(),
        screenshot_path=screenshot_path,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


# ── Path B: local cloakbrowser.launch() — sync, chạy trong thread ────────────

def _move_window_by_title(unique_title: str, x: int, y: int, w: int, h: int, retries: int = 15) -> None:
    """Tìm cửa sổ Chrome theo title độc nhất rồi move+resize."""
    import subprocess
    for _ in range(retries):
        time.sleep(0.5)
        try:
            out = subprocess.check_output(
                ["xdotool", "search", "--name", unique_title],
                stderr=subprocess.DEVNULL,
            ).decode().strip().splitlines()
            if out:
                wid = out[0]
                subprocess.run(["xdotool", "windowmove", "--sync", wid, str(x), str(y)], stderr=subprocess.DEVNULL)
                subprocess.run(["xdotool", "windowsize", "--sync", wid, str(w), str(h)], stderr=subprocess.DEVNULL)
                return
        except Exception:
            pass


def _sync_run(idx: int, seed: int, url: str, headless: bool, take_screenshot: bool) -> ProfileResult:
    t0 = time.monotonic()
    x, y, w, h = WINDOW_POSITIONS[idx]

    try:
        from cloakbrowser import launch as cloak_launch  # type: ignore
        browser = cloak_launch(headless=headless, args=[f"--fingerprint={seed}"])
        source = "cloakbrowser-local"
    except ImportError:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=headless)
        source = "playwright-plain"

    context = browser.new_context(viewport={"width": w, "height": h - 90})
    page = context.new_page()

    # Đặt title độc nhất → xdotool tìm chính xác từng cửa sổ → move
    if not headless:
        import shutil
        unique_title = f"CloakProfile{idx + 1}"
        page.evaluate(f"document.title = '{unique_title}'")
        if shutil.which("xdotool"):
            _move_window_by_title(unique_title, x, y, w, h)
        else:
            print(f"  [Profile {idx+1}] ⚠ cài xdotool: sudo apt install xdotool")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(4)  # chờ JS render (1688, Shopee, Lazada cần thời gian)

    title = page.title()
    text = page.evaluate("() => document.body ? document.body.innerText : ''")

    screenshot_path: str | None = None
    if take_screenshot:
        path = f"/tmp/profile_{idx+1}_seed{seed}.png"
        page.screenshot(path=path)
        screenshot_path = path

    print(f"  [Profile {idx+1}] ⏸  Browser mở — nhấn Enter để đóng...")
    input()
    context.close()
    browser.close()

    return ProfileResult(
        profile_idx=idx + 1, fingerprint_seed=seed, status="success",
        source=source, title=title,
        page_text_preview=text[:300].replace("\n", " ").strip(),
        screenshot_path=screenshot_path,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


WINDOW_POSITIONS: list[tuple[int, int, int, int]] = []  # set in main()


async def run_one_profile(
    idx: int, seed: int, url: str, headless: bool, take_screenshot: bool,
    executor: ThreadPoolExecutor,
) -> ProfileResult:
    print(f"  [Profile {idx+1}] seed={seed} → starting...")

    # Headed mode → bắt buộc local (Chrome trong Docker không hiện lên màn hình)
    if not headless:
        print(f"  [Profile {idx+1}] headed mode → local launch")
    else:
        try:
            # Thử CDP trước (nếu cloakserve đang chạy)
            result = await run_via_cdp(idx, seed, url, headless, take_screenshot)
            print(f"  [Profile {idx+1}] ✅ {result.latency_ms}ms via {result.source} | {result.title!r}")
            return result
        except Exception as cdp_err:
            print(f"  [Profile {idx+1}] cloakserve unavailable ({cdp_err.__class__.__name__}), dùng local launch...")

    # Fallback: chạy sync trong thread riêng
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            _sync_run, idx, seed, url, headless, take_screenshot,
        )
        print(f"  [Profile {idx+1}] ✅ {result.latency_ms}ms via {result.source} | {result.title!r}")
        return result
    except Exception as exc:
        ms = 0
        print(f"  [Profile {idx+1}] ❌ {exc}")
        return ProfileResult(
            profile_idx=idx + 1, fingerprint_seed=seed,
            status="error", error=str(exc),
        )


async def main(url: str, headless: bool, take_screenshot: bool, monitor: int) -> None:
    global WINDOW_POSITIONS
    WINDOW_POSITIONS = calc_positions(monitor)

    print(f"\n{'='*60}")
    print(f"  URL      : {url}")
    print(f"  Profiles : {PROFILES}")
    print(f"  Headless : {headless}")
    print(f"  Monitor  : {monitor} (x offset = {0 if monitor == 1 else SCREEN_W})")
    print(f"  CloakSrv : {settings.cloak_browser_url}")
    if not headless:
        print(f"\n  Window layout:")
        for i, (x, y, w, h) in enumerate(WINDOW_POSITIONS):
            print(f"    Profile {i+1}: x={x} y={y} w={w} h={h}")
    print(f"{'='*60}\n")

    t_start = time.monotonic()

    # ThreadPoolExecutor cho path sync (1 thread/profile)
    with ThreadPoolExecutor(max_workers=PROFILES) as executor:
        tasks = [
            run_one_profile(
                idx=i, seed=FINGERPRINT_SEEDS[i],
                url=url, headless=headless,
                take_screenshot=take_screenshot,
                executor=executor,
            )
            for i in range(PROFILES)
        ]
        results: list[ProfileResult] = await asyncio.gather(*tasks)

    total_ms = int((time.monotonic() - t_start) * 1000)

    print(f"\n{'='*60}")
    print(f"  RESULTS ({total_ms}ms total — {PROFILES} profiles parallel)")
    print(f"{'='*60}")
    for r in sorted(results, key=lambda x: x.profile_idx):
        icon = "✅" if r.status == "success" else "❌"
        print(f"\n  {icon} Profile {r.profile_idx} | seed={r.fingerprint_seed} | {r.latency_ms}ms | {r.source}")
        if r.status == "success":
            print(f"     title   : {r.title}")
            print(f"     preview : {(r.page_text_preview or '')[:120]}...")
            if r.screenshot_path:
                print(f"     screenshot: {r.screenshot_path}")
        else:
            print(f"     error   : {r.error}")

    success = sum(1 for r in results if r.status == "success")
    sequential_est = sum(r.latency_ms for r in results if r.status == "success")
    print(f"\n  Summary  : {success}/{PROFILES} success")
    print(f"  Parallel : {total_ms}ms  |  Sequential estimate : ~{sequential_est}ms")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test 5 CloakBrowser profiles simultaneously")
    parser.add_argument("url", help="URL to open in all profiles")
    parser.add_argument("--headed", action="store_true", help="Show browser windows")
    parser.add_argument("--screenshot", action="store_true", help="Save screenshot to /tmp/")
    parser.add_argument("--monitor", type=int, default=1, choices=[1, 2],
                        help="Màn hình để tile windows (1=trái, 2=phải). Default: 1")
    args = parser.parse_args()

    asyncio.run(main(url=args.url, headless=not args.headed,
                     take_screenshot=args.screenshot, monitor=args.monitor))

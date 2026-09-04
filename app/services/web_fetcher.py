import httpx
import html2text
from bs4 import BeautifulSoup

MAX_CONTENT_CHARS = 12_000  # keep within model context limits


class WebFetchError(Exception):
    pass


async def fetch_page_text(url: str) -> str:
    """
    Fetch URL and return clean text content (no HTML tags).
    Truncates to MAX_CONTENT_CHARS to stay within LLM context.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise WebFetchError(f"HTTP {resp.status_code} fetching {url}")
            html = resp.text
    except httpx.RequestError as exc:
        raise WebFetchError(f"Network error fetching {url}: {exc}") from exc

    # Remove script/style noise before converting
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "iframe", "noscript"]):
        tag.decompose()

    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.body_width = 0

    text = converter.handle(str(soup))

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean = "\n".join(lines)

    return clean[:MAX_CONTENT_CHARS]

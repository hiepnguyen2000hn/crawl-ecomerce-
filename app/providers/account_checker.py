import httpx

SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"


class SerpApiAccountInfo:
    def __init__(self, searches_left: int, plan_searches: int):
        self.searches_left = searches_left
        self.plan_searches = plan_searches

    @property
    def is_exhausted(self) -> bool:
        return self.searches_left <= 0


async def check_account(api_key: str) -> SerpApiAccountInfo:
    """
    Call SerpAPI account endpoint to get remaining searches.
    Returns SerpApiAccountInfo. On error, assumes searches still available.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(SERPAPI_ACCOUNT_URL, params={"api_key": api_key})
            if resp.status_code != 200:
                return SerpApiAccountInfo(searches_left=1, plan_searches=0)
            data = resp.json()
            left = data.get("plan_searches_left", data.get("searches_per_month", 0))
            total = data.get("plan_monthly_searches", data.get("searches_per_month", 0))
            return SerpApiAccountInfo(searches_left=int(left), plan_searches=int(total))
    except Exception:
        return SerpApiAccountInfo(searches_left=1, plan_searches=0)

"""FastAPI dependencies — truy cập tài nguyên dùng chung từ `app.state`.

`arq_redis` + `job_store` được khởi tạo ở lifespan (`main.py`).
"""

from fastapi import HTTPException, Request


def get_arq(request: Request):
    pool = getattr(request.app.state, "arq_redis", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Queue unavailable")
    return pool


def get_job_store(request: Request):
    store = getattr(request.app.state, "job_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Job store unavailable")
    return store

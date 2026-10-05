from fastapi import Depends, FastAPI

from app.api import require_api_key, router

app = FastAPI(title="Payments")
app.include_router(router)


@app.get("/health", dependencies=[Depends(require_api_key)])
async def health() -> dict[str, str]:
    return {"status": "ok"}

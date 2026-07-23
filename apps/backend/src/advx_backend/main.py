from fastapi import FastAPI

from advx_backend.api.http.health import router as health_router
from advx_backend.api.ws.realtime import router as realtime_router

app = FastAPI(title="ADVX Live Backend", version="0.1.0")
app.include_router(health_router)
app.include_router(realtime_router)

from fastapi import APIRouter

from app.api.v1 import advisor, alerts, auth, market, portfolio, signals, users, watchlist

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(portfolio.router)
api_router.include_router(market.router)
api_router.include_router(signals.router)
api_router.include_router(advisor.router)
api_router.include_router(alerts.router)
api_router.include_router(watchlist.router)

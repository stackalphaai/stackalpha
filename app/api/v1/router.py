from fastapi import APIRouter

from app.api.v1 import (
    admin,
    affiliate,
    analytics,
    auth,
    subscription,
    telegram,
    trading,
    users,
    wallet,
    ws,
)

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(users.router)
router.include_router(wallet.router)
router.include_router(trading.router)
router.include_router(subscription.router)
router.include_router(telegram.router)
router.include_router(affiliate.router)
router.include_router(analytics.router)
router.include_router(admin.router)
router.include_router(ws.router)

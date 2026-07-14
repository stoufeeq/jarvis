"""
Celery task: pre-generate today's daily briefing for every active user.

Target time: 8:30 AM US/Eastern (1 hour before US market open). The
beat schedule fires at BOTH 12:30 UTC and 13:30 UTC — one of those is
8:30 ET depending on DST. The task itself checks the current US/Eastern
hour and short-circuits when it isn't 8, so only the relevant firing
actually generates briefings. Keeps DST handling correct without
external scheduler libraries (Celery's built-in PersistentScheduler
doesn't do per-task timezones).

BriefingService.get_or_create_today is serialised by a per-(user,day)
Postgres advisory lock, so this task is safe to run concurrently with
the user opening the app — only one side will actually generate; the
other will find the fresh row and return it.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.briefing import BriefingService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

ET = ZoneInfo("US/Eastern")
TARGET_ET_HOUR = 8  # 8:30 AM ET = 1h before US market open


@celery_app.task(name="app.workers.tasks.briefing_pregenerate.pregenerate_today", bind=True)
def pregenerate_today(self):
    asyncio.run(_run())


async def _run() -> None:
    now_et = datetime.now(ET)
    if now_et.hour != TARGET_ET_HOUR:
        log.info(
            "Briefing pre-gen skipping — current ET hour is %d (need %d). "
            "This is the DST-mirror firing.",
            now_et.hour, TARGET_ET_HOUR,
        )
        return

    # Read the user list in one session, then commit + close so the
    # per-user advisory locks acquired below don't accumulate on a single
    # long-lived transaction (which would keep locks held across the
    # entire user loop and needlessly block concurrent app requests).
    async with AsyncSessionLocal() as db:
        users = list((await db.execute(
            select(User).where(User.is_active.is_(True))
        )).scalars().all())

    if not users:
        log.info("Briefing pre-gen: no active users to process")
        return

    generated = cached = errors = 0
    for user in users:
        try:
            # Fresh session per user so the advisory lock acquired inside
            # BriefingService is released as soon as this user's briefing
            # is committed — not held until the whole loop finishes.
            async with AsyncSessionLocal() as db:
                svc = BriefingService(db)
                briefing = await svc.get_or_create_today(user)
                await db.commit()
                # Distinguish "we just made it" from "found existing": the
                # row's generated_at is within the last 60s if we generated.
                age = (datetime.now(briefing.generated_at.tzinfo) - briefing.generated_at).total_seconds()
                if age < 60:
                    generated += 1
                    log.info(
                        "Briefing pre-gen: created for user %s (briefing_id=%s)",
                        user.email, briefing.id,
                    )
                else:
                    cached += 1
                    log.info(
                        "Briefing pre-gen: user %s already had today's briefing "
                        "(age %.0fs), skipped",
                        user.email, age,
                    )
        except Exception as exc:
            errors += 1
            log.warning(
                "Briefing pre-gen: failed for user %s: %s",
                user.id, exc,
            )

    log.info(
        "Briefing pre-gen done: generated=%d, already-cached=%d, errors=%d",
        generated, cached, errors,
    )

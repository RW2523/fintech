"""Temporal worker entrypoint for the underwriting task queue."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from cio_common.settings import get_settings
from workflows.activities import ACTIVITIES
from workflows.early_warning import EarlyWarningCase
from workflows.shared import TASK_QUEUE
from workflows.underwriting import UnderwriteCase

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    log.info("worker starting on %s", TASK_QUEUE)
    async with Worker(
        client, task_queue=TASK_QUEUE, workflows=[UnderwriteCase, EarlyWarningCase], activities=ACTIVITIES
    ):
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

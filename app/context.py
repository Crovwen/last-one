from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from aiogram import Bot

from app.config import Config
from app.services.jobs import JobRunner
from app.services.settings import SettingsService


@dataclass
class AppContext:
    config: Config
    pool: asyncpg.Pool
    settings: SettingsService
    jobs: JobRunner
    bot: Bot

"""Ambiente do Alembic — assíncrono, lendo a URL do ambiente."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from plataforma.db.esquema import metadata

config = context.config
if url := os.environ.get("PLATAFORMA_BANCO"):
    config.set_main_option("sqlalchemy.url", url)

alvo = metadata


def offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=alvo,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrar(conexao) -> None:
    context.configure(connection=conexao, target_metadata=alvo)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    motor = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with motor.connect() as conexao:
        await conexao.run_sync(_migrar)
    await motor.dispose()


if context.is_offline_mode():
    offline()
else:
    asyncio.run(online())

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.checkpoints import psycopg_database_url


async def setup_checkpointer() -> None:
    async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
        await checkpointer.setup()


if __name__ == "__main__":
    asyncio.run(setup_checkpointer())

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from app.agents.checkpoints import psycopg_database_url
from app.agents.live_video.graph import build_live_video_graph
from app.agents.live_video.schemas import LiveVideoState
from app.db.models import ClipCandidate, MediaTask
from app.db.session import get_sessionmaker


async def resume_live_video_task(task_id: str) -> None:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        task = await session.get(MediaTask, task_id)
        if task is None:
            return
        clips = (
            await session.scalars(
                select(ClipCandidate)
                .where(ClipCandidate.task_id == task_id)
                .order_by(ClipCandidate.ordinal)
            )
        ).all()
    update: LiveVideoState = {
        "status": task.status,
        "progress": task.progress,
        "transcript_ref": None,
        "candidate_refs": [f"media-clip://{clip.id}" for clip in clips],
        "qc": [clip.qc_result for clip in clips],
        "review_status": "PENDING",
    }
    async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
        await checkpointer.setup()
        graph = build_live_video_graph(checkpointer)
        config = {
            "configurable": {
                "thread_id": task.thread_id,
            }
        }
        await graph.aupdate_state(config, update)
        await graph.ainvoke(None, config)

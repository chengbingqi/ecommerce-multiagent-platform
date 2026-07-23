from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from app.agents.checkpoints import psycopg_database_url
from app.agents.customer_service.repository import RepositoryFailure
from app.agents.live_video.graph import build_live_video_graph
from app.agents.live_video.resume import resume_live_video_task
from app.agents.live_video.schemas import LiveVideoOutput, LiveVideoState
from app.db.models import AgentRun, MediaTask, Principal
from app.media.schemas import ClipTaskCreateRequest, TaskView
from app.media.service import MediaService


class LiveVideoAgentService:
    def __init__(
        self, media: MediaService | None = None, *, trusted_identity: bool = False
    ) -> None:
        self.media = media or MediaService(trusted_identity=trusted_identity)

    async def create_run(self, asset_id: str, request: ClipTaskCreateRequest) -> TaskView:
        assert request.principal_id is not None
        task = await self.media.create_clip_task(asset_id, request, enqueue_worker=False)
        asset = await self.media.repository.get_asset(asset_id, request.principal_id)
        state: LiveVideoState = {
            "agent_type": "live_video",
            "asset_id": asset_id,
            "task_id": task.task_id,
            "run_id": task.run_id,
            "thread_id": task.thread_id,
            "trace_id": task.trace_id,
            "principal_id": request.principal_id,
            "store_id": asset.store_id,
            "status": "QUEUED",
            "progress": 0,
            "pipeline_version": task.pipeline_version,
            "transcript_ref": None,
            "candidate_refs": [],
            "qc": [],
            "review_status": "PENDING",
            "warnings": [],
            "error": None,
        }
        async with AsyncPostgresSaver.from_conn_string(psycopg_database_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_live_video_graph(checkpointer)
            await graph.ainvoke(
                state,
                {
                    "configurable": {
                        "thread_id": task.thread_id,
                    }
                },
            )
        if task.status == "QUEUED":
            try:
                self.media.enqueue(task.asset_id, task.task_id, task.run_id, task.pipeline_version)
            except Exception as exc:
                raise RepositoryFailure(
                    "TRANSIENT_ERROR", "media queue is temporarily unavailable"
                ) from exc
        return task

    async def resume_from_task(self, task_id: str) -> None:
        await resume_live_video_task(task_id)

    async def get_run(self, run_id: str, principal_id: str) -> LiveVideoOutput:
        repository = self.media.repository
        async with repository.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.agent_type != "live_video":
                raise RepositoryFailure("NOT_FOUND", "run was not found")
            actor = await session.get(Principal, principal_id)
            if actor is None or not actor.active:
                raise RepositoryFailure("UNAUTHENTICATED", "principal is not active")
            if actor.role != "admin" and actor.id != run.principal_id:
                raise RepositoryFailure("FORBIDDEN", "run is outside the principal scope")
            task = await session.scalar(select(MediaTask).where(MediaTask.run_id == run_id))
            if task is None:
                raise RepositoryFailure("NOT_FOUND", "media task was not found")
        clips = await repository.list_clips(task.id, principal_id)
        transcript_ref = None
        try:
            transcript_ref = (await repository.transcript(task.id, principal_id)).transcript_ref
        except RepositoryFailure as exc:
            if exc.code != "NOT_FOUND":
                raise
        candidates = [
            {
                "clip_id": clip.clip_id,
                "evidence_ref": f"media-clip://{clip.clip_id}",
                "start_seconds": str(clip.start_seconds),
                "end_seconds": str(clip.end_seconds),
                "score": str(clip.score),
                "review_status": clip.review_status,
            }
            for clip in clips
        ]
        return LiveVideoOutput(
            run_id=run.run_id,
            thread_id=run.thread_id,
            trace_id=run.trace_id,
            task_id=task.id,
            asset_id=task.asset_id,
            pipeline_version=task.pipeline_version,
            status=task.status,
            progress=task.progress,
            transcript_ref=transcript_ref,
            candidates=candidates,
            timestamps=[
                {
                    "clip_id": clip.clip_id,
                    "start_seconds": str(clip.start_seconds),
                    "end_seconds": str(clip.end_seconds),
                }
                for clip in clips
            ],
            scores=[{"clip_id": clip.clip_id, "score": str(clip.score)} for clip in clips],
            qc=[clip.qc for clip in clips],
            review_status=(
                "PENDING" if any(clip.review_status == "PENDING" for clip in clips) else "REVIEWED"
            ),
            warnings=[],
            error=(
                {"error_code": task.error_code, "message": "media task failed"}
                if task.error_code
                else None
            ),
        )

from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    IngestPort,
    IngestReceipt,
    TextInput,
)


class IngestPipelineUnavailableError(RuntimeError):
    pass


class IngestGateway:
    """Stable API-facing gateway for a pipeline configured after app creation."""

    def __init__(self) -> None:
        self._port: IngestPort | None = None

    @property
    def available(self) -> bool:
        return self._port is not None

    def configure(self, port: IngestPort) -> None:
        if self._port is not None:
            raise RuntimeError("the ingest gateway is already configured")
        self._port = port

    def clear(self) -> None:
        self._port = None

    async def submit_text(self, input: TextInput) -> IngestReceipt:
        return await self._require_port().submit_text(input)

    async def submit_audio(self, input: AudioInput) -> IngestReceipt:
        return await self._require_port().submit_audio(input)

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt:
        return await self._require_port().commit_audio(commit)

    async def notify_voice_activity(self, session_id: str, occurred_at_ms: int) -> None:
        await self._require_port().notify_voice_activity(session_id, occurred_at_ms)

    async def submit_frame(self, input: FrameInput) -> IngestReceipt:
        return await self._require_port().submit_frame(input)

    def _require_port(self) -> IngestPort:
        if self._port is None:
            raise IngestPipelineUnavailableError("the ingest pipeline is not configured")
        return self._port

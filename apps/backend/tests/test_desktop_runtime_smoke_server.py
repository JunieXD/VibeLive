import importlib.util
from pathlib import Path

import pytest

from advx_backend.application.recorded_scenario import (
    _RecordedViewerProvider,
    build_recorded_runtime_fixture,
)
from advx_backend.contracts.replay import (
    RecordedOutputConsumption,
    RecordedProviderOutput,
    RecordedReplayEvidence,
)


class _HistoryLedger:
    def __init__(self, output: RecordedProviderOutput) -> None:
        self._output = output
        self.roles: list[str] = []

    def consume(
        self,
        role: str,
        *,
        runtime_request_id: str | None = None,
    ) -> dict[str, object]:
        del runtime_request_id
        self.roles.append(role)
        return dict(self._output.output)


def _server_module():
    path = Path(__file__).parents[1] / "scripts" / "desktop_runtime_smoke_server.py"
    spec = importlib.util.spec_from_file_location("desktop_runtime_smoke_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_smoke_fixture_uses_production_graph_without_external_transports(
    tmp_path: Path,
) -> None:
    module = _server_module()
    bundle = module._bundle()
    fixture = build_recorded_runtime_fixture(
        data_directory=tmp_path,
        local_token="runtime-smoke-test-token",
        bundle=bundle,
    )

    assert {
        output.provider_role for output in bundle.recorded_provider_outputs
    } == {"viewer", "memory", "visual_summary", "asr"}
    assert fixture.app.state.runtime is fixture.runtime
    assert fixture.runtime.viewer_runtime_coordinator is not None
    assert fixture.runtime.database.path.parent == tmp_path
    assert fixture.external_transport_call_count == 0

    async with fixture.app.router.lifespan_context(fixture.app):
        assert fixture.runtime.database.started is True
    assert fixture.runtime.database.started is False


@pytest.mark.asyncio
async def test_recorded_history_summary_is_whitelisted_consumed_and_evidenced() -> None:
    output = RecordedProviderOutput(
        generation_request_id="history-1",
        provider_role="history_summary",
        output={"summary": "录制的历史摘要"},
    )
    ledger = _HistoryLedger(output)
    provider = _RecordedViewerProvider(ledger)

    summary = await provider.summarize_history(
        session_id="session",
        audience_epoch=1,
        existing_summary=None,
        older_history="待压缩历史",
    )
    consumption = RecordedOutputConsumption(
        provider_role="history_summary",
        generation_request_id="history-1",
        call_index=1,
    )
    evidence = RecordedReplayEvidence(
        decisions=[],
        selected_viewer_ids=[],
        barrages=[],
        memories=[],
        traces=[],
        consumed_provider_roles=["history_summary"],
        consumed_provider_outputs=[consumption],
    )

    assert summary == "录制的历史摘要"
    assert provider.history_calls == 1
    assert ledger.roles == ["history_summary"]
    assert evidence.consumed_provider_roles == ["history_summary"]

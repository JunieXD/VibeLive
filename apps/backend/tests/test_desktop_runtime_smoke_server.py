import importlib.util
from pathlib import Path

import pytest

from advx_backend.application.recorded_scenario import build_recorded_runtime_fixture


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

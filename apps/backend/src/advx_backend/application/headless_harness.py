from pathlib import Path
from typing import Any

from pydantic import ValidationError

from advx_backend.application.recorded_scenario import (
    RecordedScenarioError,
    run_recorded_scenario,
)
from advx_backend.application.replay_service import (
    LiveReplayCallable,
    LiveReplayProvider,
    RecordedReplayCallable,
    ReplayService,
)
from advx_backend.contracts.replay import ReplayRequest
from advx_backend.infrastructure.logging.trace_store import UnsafeTraceArtifactError

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_UNSAFE_ARTIFACT = 3
EXIT_REPLAY_FAILED = 4
EXIT_SCENARIO_FAILED = 5


class HeadlessHarness:
    def __init__(
        self,
        *,
        data_directory: Path,
        live_provider: LiveReplayProvider | LiveReplayCallable | None = None,
        recorded_runner: RecordedReplayCallable | None = None,
    ) -> None:
        self._data_directory = data_directory.resolve()
        self._data_directory.mkdir(parents=True, exist_ok=True)
        self._replay_service = ReplayService(
            live_provider=live_provider,
            recorded_runner=recorded_runner,
            recorded_data_directory=self._data_directory / "replay",
        )

    async def execute(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            command = payload.get("command")
            if command not in {"replay", "scenario"}:
                raise ValueError("unsupported command")
            request = ReplayRequest.model_validate(payload.get("request"))
            if command == "scenario":
                result: Any = await run_recorded_scenario(
                    data_directory=self._data_directory,
                    request=request,
                )
            else:
                result = await self._replay_service.replay(request)
                result = result.model_dump(mode="json")
        except UnsafeTraceArtifactError:
            return EXIT_UNSAFE_ARTIFACT, self._error("unsafe_artifact")
        except RecordedScenarioError as error:
            response = self._error("scenario_failed")
            response["metadata"]["failure_artifact"] = str(error.artifact_path)
            return EXIT_SCENARIO_FAILED, response
        except (ValidationError, TypeError, ValueError):
            return EXIT_INVALID_INPUT, self._error("invalid_input")
        except Exception:
            return EXIT_REPLAY_FAILED, self._error("replay_failed")

        return EXIT_OK, {
            "ok": True,
            "result": result,
            "metadata": {
                "seed": request.bundle.seed,
                "virtual_clock_start_ms": request.bundle.virtual_clock_start_ms,
                "data_directory": str(self._data_directory),
                "isolated_data_directory": True,
                "sqlite_path": str(self._data_directory / "advx.sqlite3"),
                "port": 0,
                "token_scope": "headless",
                "room_id": request.bundle.canonical_runtime_spec.room.room_id,
            },
        }

    def _error(self, code: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": code},
            "metadata": {
                "data_directory": str(self._data_directory),
                "isolated_data_directory": True,
            },
        }

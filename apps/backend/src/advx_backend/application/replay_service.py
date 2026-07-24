import hashlib
import inspect
import json
import random
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from advx_backend.contracts.replay import (
    LiveReplayEvidence,
    RecordedReplayEvidence,
    ReplayBundle,
    ReplayMode,
    ReplayRequest,
    ReplayResult,
)
from advx_backend.infrastructure.logging.trace_store import assert_redacted_artifact


class LiveReplayProvider(Protocol):
    def replay(
        self,
        bundle: ReplayBundle,
    ) -> Awaitable[LiveReplayEvidence] | LiveReplayEvidence: ...


LiveReplayCallable = Callable[
    [ReplayBundle],
    Awaitable[LiveReplayEvidence] | LiveReplayEvidence,
]
RecordedReplayCallable = Callable[
    ...,
    Awaitable[RecordedReplayEvidence] | RecordedReplayEvidence,
]


@dataclass(slots=True)
class ReplayVirtualClock:
    current_ms: int

    def now_ms(self) -> int:
        return self.current_ms

    def advance_to(self, timestamp_ms: int) -> None:
        if timestamp_ms < self.current_ms:
            raise ValueError("virtual clock cannot move backwards")
        self.current_ms = timestamp_ms


@dataclass(frozen=True, slots=True)
class RecordedReplayExecution:
    seed: int
    random: random.Random
    clock: ReplayVirtualClock
    data_directory: Path
    sqlite_path: Path
    port: int
    local_token: str
    room_id: str


class ReplayService:
    def __init__(
        self,
        *,
        live_provider: LiveReplayProvider | LiveReplayCallable | None = None,
        recorded_runner: RecordedReplayCallable | None = None,
        recorded_data_directory: Path | None = None,
    ) -> None:
        self._live_provider = live_provider
        self._recorded_runner = recorded_runner
        self._recorded_data_directory = recorded_data_directory

    async def replay(self, request: ReplayRequest) -> ReplayResult:
        assert_redacted_artifact(request.bundle)
        completed_at_ms = self._completion_time(request.bundle)

        if request.mode is ReplayMode.RECORDED:
            request.bundle.assert_recorded_outputs_integrity()
            request.bundle.assert_recorded_output_correlations()
            first = await self._execute_recorded(request.bundle, run_number=1)
            second = await self._execute_recorded(request.bundle, run_number=2)
            first_digest = self._digest_recorded_execution(request.bundle, first)
            second_digest = self._digest_recorded_execution(request.bundle, second)
            if first_digest != second_digest:
                raise RuntimeError("recorded runtime replay produced nondeterministic evidence")
            return ReplayResult(
                bundle_id=request.bundle.bundle_id,
                mode=request.mode,
                deterministic_proof=True,
                credentialed_provider_proof=False,
                event_count=len(request.bundle.events),
                trace_count=len(request.bundle.traces),
                completed_at_ms=completed_at_ms,
                replay_digest=first_digest,
                recorded_evidence=first,
                external_transport_call_count=0,
            )

        if not request.allow_external_provider_calls:
            raise ValueError("live replay requires explicit external Provider opt-in")
        if self._live_provider is None:
            raise RuntimeError("live replay provider is not configured")

        provider = self._live_provider
        outcome = (
            provider.replay(request.bundle)
            if hasattr(provider, "replay")
            else provider(request.bundle)
        )
        evidence = await outcome if inspect.isawaitable(outcome) else outcome
        if not isinstance(evidence, LiveReplayEvidence):
            raise RuntimeError("live replay provider did not return verified provenance")
        return ReplayResult(
            bundle_id=request.bundle.bundle_id,
            mode=request.mode,
            deterministic_proof=False,
            credentialed_provider_proof=True,
            event_count=len(request.bundle.events),
            trace_count=len(request.bundle.traces),
            completed_at_ms=completed_at_ms,
            provider_profile_id=evidence.provider_profile_id,
            external_transport_call_count=evidence.external_transport_call_count,
        )

    async def _execute_recorded(
        self,
        bundle: ReplayBundle,
        *,
        run_number: int,
    ) -> RecordedReplayEvidence:
        runner = self._recorded_runner
        if runner is None:
            from advx_backend.application.recorded_scenario import (
                execute_recorded_runtime,
            )

            runner = execute_recorded_runtime

        if self._recorded_data_directory is None:
            temporary = tempfile.TemporaryDirectory(
                prefix=f"advx-recorded-replay-{run_number}-"
            )
            data_directory = Path(temporary.name)
        else:
            self._recorded_data_directory.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(
                prefix=f"run-{run_number}-",
                dir=self._recorded_data_directory,
            )
            data_directory = Path(temporary.name)
        try:
            execution = RecordedReplayExecution(
                seed=bundle.seed,
                random=random.Random(bundle.seed),
                clock=ReplayVirtualClock(bundle.virtual_clock_start_ms),
                data_directory=data_directory,
                sqlite_path=data_directory / "advx.sqlite3",
                port=0,
                local_token=hashlib.sha256(
                    f"{bundle.bundle_id}\0{data_directory}".encode()
                ).hexdigest(),
                room_id=bundle.canonical_runtime_spec.room.room_id,
            )
            outcome = (
                runner(bundle, data_directory, execution)
                if len(inspect.signature(runner).parameters) >= 3
                else runner(bundle, data_directory)
            )
            evidence = await outcome if inspect.isawaitable(outcome) else outcome
        finally:
            temporary.cleanup()
        if isinstance(evidence, dict):
            evidence = RecordedReplayEvidence.model_validate(evidence)
        if not isinstance(evidence, RecordedReplayEvidence):
            raise RuntimeError("recorded runtime did not return replay evidence")
        if evidence.external_transport_call_count != 0:
            raise RuntimeError("recorded runtime attempted an external transport call")
        return evidence

    @staticmethod
    def _digest_recorded_execution(
        bundle: ReplayBundle,
        evidence: RecordedReplayEvidence,
    ) -> str:
        payload = {
            "bundle_id": bundle.bundle_id,
            "seed": bundle.seed,
            "virtual_clock_start_ms": bundle.virtual_clock_start_ms,
            "config_hash": bundle.config_hash,
            "runtime_evidence": evidence.model_dump(mode="json"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _completion_time(bundle: ReplayBundle) -> int:
        if not bundle.events:
            return bundle.virtual_clock_start_ms
        return max(
            bundle.virtual_clock_start_ms,
            max(event.occurred_at_ms for event in bundle.events),
        )

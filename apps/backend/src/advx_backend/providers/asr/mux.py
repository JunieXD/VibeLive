import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Final

from advx_backend.application.ports.asr import (
    AsrProvider,
    AudioChunk,
    AudioSource,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)
_RESULT_RETRY_BACKOFF_SECONDS: Final = 0.01


class _EndOfResults:
    pass


_END_OF_RESULTS: Final = _EndOfResults()
_ResultItem = TranscriptSegment | _EndOfResults


class AsrProviderMux:
    """Route each audio source to an independent provider lifecycle."""

    def __init__(self, providers: Mapping[AudioSource, AsrProvider]) -> None:
        if set(providers) != set(AudioSource):
            raise ValueError("ASR mux requires one provider for every audio source")
        if len({id(provider) for provider in providers.values()}) != len(AudioSource):
            raise ValueError("ASR mux requires independent provider instances")
        self._providers = dict(providers)
        self._results: asyncio.Queue[_ResultItem] = asyncio.Queue()
        self._forwarders: list[asyncio.Task[None]] = []
        self._available: set[AudioSource] = set()

    async def start(self) -> None:
        self._results = asyncio.Queue()
        self._forwarders.clear()
        self._available.clear()
        for source, provider in self._providers.items():
            try:
                await provider.start()
            except Exception as error:
                logger.error(
                    "ASR source failed to start",
                    extra={"audio_source": source.value, "error_type": type(error).__name__},
                )
                continue
            self._available.add(source)
            self._forwarders.append(
                asyncio.create_task(
                    self._forward(source, provider),
                    name=f"asr-mux:{source.value}",
                )
            )
        if not self._available:
            raise RuntimeError("all ASR sources failed to start")

    async def push_audio(self, chunk: AudioChunk) -> None:
        await self._provider(chunk.source).push_audio(chunk)

    async def commit(self, source: AudioSource = AudioSource.MICROPHONE) -> None:
        await self._provider(source).commit(source)

    def results(self) -> AsyncIterator[TranscriptSegment]:
        return self._results_iter()

    async def stop(self) -> None:
        forwarders = tuple(self._forwarders)
        self._forwarders.clear()
        for task in forwarders:
            task.cancel()
        if forwarders:
            await asyncio.gather(*forwarders, return_exceptions=True)
        cleanup_results = await asyncio.gather(
            *(provider.stop() for provider in self._providers.values()),
            return_exceptions=True,
        )
        self._available.clear()
        await self._results.put(_END_OF_RESULTS)
        cleanup_errors = [
            result for result in cleanup_results if isinstance(result, BaseException)
        ]
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise BaseExceptionGroup("multiple ASR providers failed to stop", cleanup_errors)

    async def _results_iter(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            item = await self._results.get()
            if item is _END_OF_RESULTS:
                return
            yield item

    async def _forward(self, source: AudioSource, provider: AsrProvider) -> None:
        try:
            while source in self._available:
                try:
                    async for segment in provider.results():
                        if segment.source is not source:
                            logger.error(
                                "ASR provider returned a mismatched audio source",
                                extra={
                                    "audio_source": source.value,
                                    "returned_audio_source": segment.source.value,
                                },
                            )
                            return
                        await self._results.put(segment)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.error(
                        "ASR source result segment failed",
                        extra={
                            "audio_source": source.value,
                            "error_type": type(error).__name__,
                        },
                    )
                    await asyncio.sleep(_RESULT_RETRY_BACKOFF_SECONDS)
                    continue
                logger.warning(
                    "ASR source result stream ended unexpectedly",
                    extra={"audio_source": source.value},
                )
                return
        finally:
            self._available.discard(source)

    def _provider(self, source: AudioSource) -> AsrProvider:
        try:
            provider = self._providers[source]
        except KeyError as error:
            raise ValueError(f"unsupported audio source: {source}") from error
        if source not in self._available:
            raise RuntimeError(f"ASR source is unavailable: {source.value}")
        return provider

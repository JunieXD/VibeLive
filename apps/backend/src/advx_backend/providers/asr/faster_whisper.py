from dataclasses import dataclass


@dataclass(frozen=True)
class FasterWhisperConfig:
    model_name: str
    device: str = "cpu"
    compute_type: str = "int8"


class FasterWhisperAsrProvider:
    """Adapter boundary for faster-whisper and Silero VAD.

    Streaming, segmentation and model selection remain unimplemented until the
    audio-format and latency Spikes are complete.
    """

    def __init__(self, config: FasterWhisperConfig) -> None:
        self.config = config

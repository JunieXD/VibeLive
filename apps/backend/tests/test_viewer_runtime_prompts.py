from advx_backend.providers.model.viewer_runtime import (
    _viewer_system_prompt,
    _window_batch_system_prompt,
)


def test_barrage_only_viewer_prompt_does_not_offer_silence() -> None:
    prompt = _viewer_system_prompt(allow_silence=False)

    assert "action=silence" not in prompt
    assert '"action":"silence"' not in prompt
    assert "intent values are exactly" in prompt
    assert "silence" not in prompt


def test_viewer_prompt_offers_silence_only_when_enabled() -> None:
    assert "action=silence" in _viewer_system_prompt(allow_silence=True)


def test_barrage_only_window_batch_requires_all_selected_viewers() -> None:
    prompt = _window_batch_system_prompt(allow_silence=False)

    assert "exactly once each" in prompt
    assert "exactly max_candidates candidates" in prompt
    assert "omit silent viewers" not in prompt


def test_window_batch_prompt_can_omit_silent_viewers_when_enabled() -> None:
    assert "omit silent viewers" in _window_batch_system_prompt(allow_silence=True)

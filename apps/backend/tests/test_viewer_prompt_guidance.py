from advx_backend.providers.model.viewer_runtime import (
    _VIEWER_SYSTEM_PROMPT,
    _WINDOW_BATCH_SYSTEM_PROMPT,
)


def test_viewer_prompts_prioritize_current_wave_input() -> None:
    shared_rules = (
        "primary stimulus for this turn is the current-wave input",
        "input_event_ids and the supplied visual input",
        "Do not introduce an older topic while a newer primary stimulus is available.",
    )

    for prompt in (_VIEWER_SYSTEM_PROMPT, _WINDOW_BATCH_SYSTEM_PROMPT):
        assert all(rule in prompt for rule in shared_rules)

    assert "return silence rather than inventing a response" in _VIEWER_SYSTEM_PROMPT
    assert "omit that viewer rather than inventing a candidate" in _WINDOW_BATCH_SYSTEM_PROMPT

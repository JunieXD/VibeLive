import json
from pathlib import Path

import pytest

from viewer_runtime_recorded_evidence import collect_evidence


@pytest.mark.asyncio
async def test_cs2_recorded_replay_hot_update_and_call_identity(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    fixture = json.loads(
        (
            root / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json"
        ).read_text(encoding="utf-8")
    )
    fixture_event_types = [
        event["event_type"] for event in fixture["bundle"]["events"]
    ]
    assert fixture_event_types == [
        "runtime.hot_update_committed",
        "observation.cs2.highlight",
        "director.completed",
        "viewer.completed",
        "visual_summary.completed",
        "memory.completed",
        "asr.completed",
    ]

    evidence = await collect_evidence(
        root / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json",
        data_directory=tmp_path,
    )
    expected = json.loads(
        (
            root
            / "tests"
            / "e2e"
            / "cs2_viewer_runtime_recorded_evidence.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence == expected
    assert evidence["replay"] == {
        "exit_code": 0,
        "deterministic_proof": True,
        "credentialed_provider_proof": False,
        "live_provider_calls": 0,
        "external_transport_call_count": 0,
        "event_count": len(fixture_event_types),
    }
    assert evidence["hot_update"]["initial_counts"] == {
        "abstract_radio": 3,
        "cheat_suspector": 1,
        "clip_alarm": 1,
        "fun_seeker": 3,
        "grudge_keeper": 2,
        "hardmouth_antifan": 3,
        "instigator": 3,
        "jinx_machine": 2,
        "meme_archivist": 3,
        "parrot_unit": 2,
        "praise_then_bite": 1,
        "reaction_qmark": 3,
        "room_historian": 1,
    }
    assert evidence["hot_update"]["updated_counts"] == {
        "abstract_radio": 3,
        "cheat_suspector": 1,
        "clip_alarm": 1,
        "fun_seeker": 3,
        "grudge_keeper": 2,
        "hardmouth_antifan": 3,
        "instigator": 3,
        "jinx_machine": 2,
        "meme_archivist": 3,
        "parrot_unit": 2,
        "praise_then_bite": 1,
        "reaction_qmark": 3,
        "room_historian": 1,
    }
    assert len(evidence["hot_update"]["retained_viewer_ids"]) == 28
    assert evidence["hot_update"]["added_viewer_ids"] == []
    assert evidence["hot_update"]["removed_viewer_ids"] == []
    assert evidence["hot_update"]["reset_viewer_ids"] == []
    assert evidence["call_identity"]["selected_viewer_ids"] == evidence[
        "call_identity"
    ]["request_viewer_ids"]
    assert {
        item["persona_id"]
        for item in evidence["call_identity"]["request_identity"]
    } == {"instigator"}
    assert all(evidence["claims"].values())
    assert evidence["not_proven"] == ["electron_ui", "credentialed_live_provider"]

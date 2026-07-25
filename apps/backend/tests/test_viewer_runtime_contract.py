from advx_backend.contracts.viewer_runtime import (
    ViewerAction,
    ViewerGenerationResponse,
)


def test_viewer_generation_response_accepts_six_barrages() -> None:
    response = ViewerGenerationResponse(
        generation_request_id="request-1",
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
        action=ViewerAction.BARRAGE,
        texts=[f"弹幕 {index}" for index in range(1, 7)],
        reaction_type="comment",
    )

    assert response.texts == [f"弹幕 {index}" for index in range(1, 7)]

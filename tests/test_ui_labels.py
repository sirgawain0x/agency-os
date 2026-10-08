from core.ui_labels import PIPELINE_STAGES, source_label, stage_label


def test_stage_label_known():
    assert stage_label("demo_scheduled") == "Demo booked"
    assert stage_label("cold") == "Not contacted"


def test_stage_label_unknown():
    assert stage_label("custom_stage") == "Custom Stage"


def test_source_label():
    assert source_label("irs_bmf") == "IRS BMF"
    assert source_label("unknown_source") == "Unknown Source"


def test_pipeline_stages_includes_closed():
    assert "closed_won" in PIPELINE_STAGES

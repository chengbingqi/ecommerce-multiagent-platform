from mcp_servers.common.errors import ERROR_CODES
from mcp_servers.common.registry import BUSINESS_SPECS, KNOWLEDGE_SPECS, MEDIA_SPECS


def test_tool_names_and_versions_are_stable() -> None:
    stage2_business = {
        "get_order",
        "get_logistics",
        "query_inventory",
        "get_sales_metrics",
        "check_refund_eligibility",
        "create_refund_request",
    }
    assert stage2_business.issubset(BUSINESS_SPECS)
    assert set(BUSINESS_SPECS) == stage2_business | {"get_operations_metrics"}
    assert set(KNOWLEDGE_SPECS) == {
        "search_policy",
        "retrieve_sop",
        "get_document_chunk",
    }
    assert set(MEDIA_SPECS) == {
        "probe_media",
        "submit_transcription",
        "detect_scenes",
        "score_highlights",
        "render_clip",
        "save_asset",
    }
    for name, spec in BUSINESS_SPECS.items():
        assert spec.tool_version == ("1.1.0" if name == "get_operations_metrics" else "1.0.0")
        assert spec.input_schema["type"] == "object"
        assert spec.output_schema["type"] == "object"
        assert spec.timeout_seconds > 0
        assert set(ERROR_CODES).issubset(spec.error_codes)
    for spec in (*KNOWLEDGE_SPECS.values(), *MEDIA_SPECS.values()):
        assert spec.tool_version == "1.0.0"
        assert spec.input_schema["type"] == "object"
        assert spec.output_schema["type"] == "object"
        assert spec.timeout_seconds > 0
        assert set(ERROR_CODES).issubset(spec.error_codes)

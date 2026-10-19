import base64
import json

import pytest
from mcp.types import CallToolResult

from tapir_archicad_mcp.tools.custom.functions import archicad_get_job, archicad_read_tool

# 1x1 PNG
PNG_B64 = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
).decode()
JPG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 16).decode()
PREVIEW_ARGS = {"port": 19723, "params": {"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}}


@pytest.mark.asyncio
async def test_preview_image_is_returned_as_image_content(fake_archicad):
    fake_archicad.on_tapir_command("GetElementPreviewImage", {"previewImage": PNG_B64})

    result = await archicad_read_tool("elements_get_element_preview_image", PREVIEW_ARGS)

    assert isinstance(result, CallToolResult)
    assert result.content[1].type == "image"
    assert result.content[1].data == PNG_B64
    assert result.content[1].mime_type == "image/png"
    assert PNG_B64 not in json.dumps(result.structured_content)
    assert PNG_B64 not in result.content[0].text
    assert "image content" in result.structured_content["previewImage"]


@pytest.mark.asyncio
async def test_jpeg_gets_its_own_mime_type(fake_archicad):
    fake_archicad.on_tapir_command("GetElementPreviewImage", {"previewImage": JPG_B64})

    result = await archicad_read_tool("elements_get_element_preview_image", PREVIEW_ARGS)

    assert result.content[1].mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_non_image_results_are_unchanged(fake_archicad):
    fake_archicad.on_tapir_command("GetAddOnVersion", {"version": "1.7.1"})

    result = await archicad_read_tool("app_get_add_on_version", {"port": 19723})

    assert result == {"version": "1.7.1"}


@pytest.mark.asyncio
async def test_profile_previews_become_one_image_block_each(fake_archicad):
    fake_archicad.on_tapir_command(
        "API.GetProfileAttributePreview",
        {
            "previewImages": [
                {"image": {"content": PNG_B64}},
                {"error": {"code": 4005, "message": "no such profile"}},
                {"image": {"content": JPG_B64}},
            ]
        },
    )
    guid = "00000000-0000-0000-0000-000000000001"
    arguments = {
        "port": 19723,
        "params": {"attributeIds": [{"attributeId": {"guid": guid}}], "imageWidth": 64, "imageHeight": 64},
    }

    result = await archicad_read_tool("attributes_get_profile_attribute_preview", arguments)

    assert isinstance(result, CallToolResult)
    assert [block.type for block in result.content] == ["text", "image", "image"]
    assert [block.mime_type for block in result.content[1:]] == ["image/png", "image/jpeg"]
    stripped = result.structured_content["previewImages"]
    assert stripped[0]["image"]["content"] == "returned as MCP image content #1"
    assert stripped[1]["error"]["message"] == "no such profile"
    assert stripped[2]["image"]["content"] == "returned as MCP image content #2"
    assert PNG_B64 not in json.dumps(result.structured_content)


def test_image_fields_name_registered_tools(fake_archicad):
    from tapir_archicad_mcp.images import IMAGE_FIELDS, NESTED_IMAGE_LISTS
    from tapir_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY

    for name in [*IMAGE_FIELDS, *NESTED_IMAGE_LISTS]:
        assert name in TOOL_CALLABLE_REGISTRY, name


@pytest.mark.asyncio
async def test_get_job_returns_image_content(fake_archicad):
    fake_archicad.on_tapir_command("GetElementPreviewImage", {"previewImage": PNG_B64})
    await archicad_read_tool("elements_get_element_preview_image", PREVIEW_ARGS)
    handle = (await archicad_get_job())["jobs"][0]["jobHandle"]

    result = await archicad_get_job(handle)

    assert isinstance(result, CallToolResult)
    assert result.content[1].type == "image"
    assert result.content[1].data == PNG_B64
    assert result.structured_content["jobHandle"] == handle
    assert result.structured_content["status"] == "completed"
    assert PNG_B64 not in json.dumps(result.structured_content)

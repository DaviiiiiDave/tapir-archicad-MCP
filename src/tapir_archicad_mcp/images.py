"""Return base64 images from Archicad as MCP image content, so the model can see them and its context stays small."""

import json
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

# Tool name -> top-level result field holding one base64 image string.
IMAGE_FIELDS: dict[str, str] = {
    "elements_get_element_preview_image": "previewImage",
    "favorites_get_favorite_preview_image": "previewImage",
    "elements_get_room_image": "roomImage",
}
# Tool name -> top-level result list whose items are {"image": {"content": <base64>}} or {"error": ...}.
NESTED_IMAGE_LISTS: dict[str, str] = {
    "attributes_get_profile_attribute_preview": "previewImages",
}


def _reference(number: int) -> str:
    return f"returned as MCP image content #{number}"


def extract_images(tool_name: str, result: Any) -> tuple[Any, list[str]]:
    """Split an image command's result into the JSON without the images and the base64 image data, in order."""
    if not isinstance(result, dict):
        return result, []

    field = IMAGE_FIELDS.get(tool_name)
    if field is not None and isinstance(result.get(field), str):
        return {**result, field: _reference(1)}, [result[field]]

    list_field = NESTED_IMAGE_LISTS.get(tool_name)
    if list_field is not None and isinstance(result.get(list_field), list):
        images: list[str] = []
        items = []
        for item in result[list_field]:
            image = item.get("image") if isinstance(item, dict) else None
            if isinstance(image, dict) and isinstance(image.get("content"), str):
                images.append(image["content"])
                item = {**item, "image": {**image, "content": _reference(len(images))}}
            items.append(item)
        if images:
            return {**result, list_field: items}, images

    return result, []


def image_content(data: str) -> ImageContent:
    # Archicad only produces png and jpg; both announce themselves in the first base64 characters.
    mime_type = "image/jpeg" if data.startswith("/9j/") else "image/png"
    return ImageContent(type="image", data=data, mime_type=mime_type)


def build_image_result(stripped: Any, images: list[str]) -> CallToolResult:
    """The JSON as text and structured content, followed by one image block per image."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(stripped, indent=2)), *map(image_content, images)],
        structured_content=stripped,
    )


def to_tool_result(tool_name: str, result: Any) -> Any:
    """Wrap an image command's result as a CallToolResult with image content; other results pass through."""
    stripped, images = extract_images(tool_name, result)
    return build_image_result(stripped, images) if images else result

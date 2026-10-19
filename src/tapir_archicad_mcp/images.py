"""Return base64 images from Archicad as MCP image content, so the model can see them and its context stays small."""

import json
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

# Tool name -> top-level result field holding a base64 image string.
IMAGE_FIELDS: dict[str, str] = {
    "elements_get_element_preview_image": "previewImage",
    "favorites_get_favorite_preview_image": "previewImage",
    "elements_get_room_image": "roomImage",
}
IMAGE_REFERENCE = "returned as MCP image content #1"


def extract_image(tool_name: str, result: Any) -> tuple[Any, str | None]:
    """Split an image command's result into the JSON without the image and the base64 image data."""
    field = IMAGE_FIELDS.get(tool_name)
    if field is None or not isinstance(result, dict) or not isinstance(result.get(field), str):
        return result, None
    return {**result, field: IMAGE_REFERENCE}, result[field]


def image_content(data: str) -> ImageContent:
    # Archicad only produces png and jpg; both announce themselves in the first base64 characters.
    mime_type = "image/jpeg" if data.startswith("/9j/") else "image/png"
    return ImageContent(type="image", data=data, mime_type=mime_type)


def to_tool_result(tool_name: str, result: Any) -> Any:
    """Wrap an image command's result as a CallToolResult with image content; other results pass through."""
    stripped, data = extract_image(tool_name, result)
    if data is None:
        return result
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(stripped, indent=2)), image_content(data)],
        structured_content=stripped,
    )

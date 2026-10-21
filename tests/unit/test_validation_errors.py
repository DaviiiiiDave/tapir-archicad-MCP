import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tapir_archicad_mcp.tools.custom.functions import archicad_read_tool


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_echo_input(fake_archicad):
    elements = [{"elementId": {"guid": f"not-a-guid-{i:05d}"}} for i in range(3000)]

    with pytest.raises(ToolError) as exc:
        await archicad_read_tool("elements_get_types_of_elements", {"port": 19723, "params": {"elements": elements}})

    message = str(exc.value)
    assert "not-a-guid" not in message
    assert len(message) < 2000
    assert "params.elements.0.elementId.guid" in message
    assert "and 2990 more errors" in message
    assert "archicad_get_command_schema('elements_get_types_of_elements')" in message

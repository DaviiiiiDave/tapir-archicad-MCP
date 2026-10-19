import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from generate_tools import classify_command  # noqa: E402
from generator_config import OFFICIAL_CONFIG, TAPIR_CONFIG, ApiSourceConfig  # noqa: E402


def _config(**lists: set[str]) -> ApiSourceConfig:
    return ApiSourceConfig(
        name="test",
        details_url="",
        model_names_url="",
        output_dir=Path("."),
        api_call_method="post_tapir_command",
        group_mapping={},
        **lists,
    )


def test_classify_returns_the_single_matching_kind():
    assert classify_command("GetX", _config(read_commands={"GetX"})) == "read"
    assert classify_command("SetX", _config(write_commands={"SetX"})) == "write"
    assert classify_command("DeleteX", _config(irreversible_commands={"DeleteX"})) == "irreversible"


@pytest.mark.parametrize("lists", [{}, {"read_commands": {"GetX"}, "write_commands": {"GetX"}}])
def test_unclassified_or_double_classified_fails_loudly(lists):
    with pytest.raises(ValueError, match="GetX"):
        classify_command("GetX", _config(**lists))


@pytest.mark.parametrize("config", [TAPIR_CONFIG, OFFICIAL_CONFIG])
def test_real_lists_are_disjoint(config):
    read, write, irreversible = config.read_commands, config.write_commands, config.irreversible_commands
    assert not (read & write) and not (read & irreversible) and not (write & irreversible)

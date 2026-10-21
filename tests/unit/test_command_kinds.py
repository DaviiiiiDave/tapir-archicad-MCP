import re

import pytest

from tapir_archicad_mcp.tools.tool_registry import (
    TOOL_CALLABLE_REGISTRY,
    TOOL_DISCOVERY_CATALOG,
    get_tool_entry,
    register_tool_for_dispatch,
)


@pytest.mark.parametrize(
    "name, kind",
    [
        ("elements_get_all_elements", "read"),
        ("elements_filter_elements", "read"),
        ("elements_highlight_elements", "read"),
        ("elements_create_walls", "write"),
        ("properties_set_property_values_of_elements", "write"),
        ("elements_delete_elements", "irreversible"),
        ("project_save_project", "irreversible"),
        ("teamwork_teamwork_send", "irreversible"),
        ("navigator_publish_publisher_set", "irreversible"),
        ("ifc_ifc_file_operation", "irreversible"),
        ("teamwork_reserve_elements", "irreversible"),
        ("teamwork_release_elements", "irreversible"),
        ("favorites_export_favorites", "irreversible"),
        ("issues_export_issues_to_bcf", "irreversible"),
        ("project_print_view", "irreversible"),
        ("library_add_files_to_embedded_library", "irreversible"),
    ],
)
def test_known_commands_are_classified(fake_archicad, name, kind):
    assert get_tool_entry(name).kind == kind


def test_every_registered_command_has_a_valid_kind(fake_archicad):
    assert {entry.kind for entry in TOOL_CALLABLE_REGISTRY.values()} <= {"read", "write", "irreversible"}


def _load_configs():
    from generator_config import OFFICIAL_CONFIG, TAPIR_CONFIG

    return {"tapir": TAPIR_CONFIG, "official": OFFICIAL_CONFIG}


def _kind_lists(config) -> dict[str, set[str]]:
    return {
        "read": config.read_commands,
        "write": config.write_commands,
        "irreversible": config.irreversible_commands,
    }


def _source_of(name: str) -> str | None:
    """'tapir' or 'official' for generated commands; None for others (custom commands, test doubles)."""
    module = TOOL_CALLABLE_REGISTRY[name].callable.__module__
    for source in ("tapir", "official"):
        if f".generated.{source}." in module:
            return source
    return None


def test_generated_kinds_match_the_generator_config(fake_archicad):
    """Every generated catalog entry is in exactly one kind list of its source config, and carries that kind."""
    configs = _load_configs()
    for entry in TOOL_DISCOVERY_CATALOG.values():
        source = _source_of(entry["name"])
        if source is None:
            continue
        kinds = [kind for kind, names in _kind_lists(configs[source]).items() if entry["title"] in names]
        assert kinds == [entry["kind"]], f"{entry['name']} ({source}:{entry['title']}): listed as {kinds}"


def test_config_lists_name_only_registered_commands(fake_archicad):
    """A stale name in a kind list (renamed or removed upstream) must not go unnoticed."""
    for source, config in _load_configs().items():
        registered = {e["title"] for e in TOOL_DISCOVERY_CATALOG.values() if _source_of(e["name"]) == source}
        for kind, names in _kind_lists(config).items():
            missing = names - registered - config.commands_to_exclude
            assert not missing, f"{source} {kind}: not registered and not excluded: {sorted(missing)}"


EXPECTED_IRREVERSIBLE = {
    "tapir": {
        "AddFilesToEmbeddedLibrary",
        "CloseProject",
        "CreateIssue",
        "DeleteClassificationItems",
        "DeleteClassificationSystems",
        "DeleteElements",
        "DeleteEmbeddedLibraryItems",
        "DeleteFavorites",
        "DeleteIssue",
        "DeleteKeynoteFolders",
        "DeleteKeynoteItems",
        "DeleteProjectInfoFields",
        "DeletePropertyDefinitions",
        "DeletePropertyGroups",
        "ExportFavorites",
        "ExportIssuesToBCF",
        "IFCFileOperation",
        "ImportClassificationsXml",
        "ImportFavorites",
        "ImportIssuesFromBCF",
        "ImportPropertiesXml",
        "OpenProject",
        "PrintView",
        "PublishPublisherSet",
        "ReleaseElements",
        "ReleaseHotlinkCacheManagement",
        "ReleaseLockableObjectSet",
        "ReserveElements",
        "ReserveHotlinkCacheManagement",
        "ReserveLockableObjectSet",
        "SaveAsModuleFile",
        "SaveProject",
        "SaveProjectAsArchive",
        "SetLibraries",
        "TeamworkReceive",
        "TeamworkSend",
    },
    "official": {
        "DeleteAttributeFolders",
        "DeleteAttributes",
        "DeleteNavigatorItems",
    },
}


def test_irreversible_set_is_pinned():
    """Moving a command out of 'irreversible' (dropping its confirm gate) needs a visible edit here."""
    for source, config in _load_configs().items():
        assert config.irreversible_commands == EXPECTED_IRREVERSIBLE[source], source


# Read titles that start with a mutating verb, each with a justification. No current read command matches
# (e.g. ChangeSelectionOfElements starts with 'Change', which is not in the pattern).
READ_NAME_ALLOWLIST: set[str] = set()
MUTATING_PREFIX = re.compile(
    r"^(Create|Delete|Set|Move|Rename|Save|Publish|Reserve|Release|Teamwork|Import|Export|Add|Remove|Modify|Apply|Open|Close)"
)


def test_no_read_command_has_a_mutating_name():
    for source, config in _load_configs().items():
        suspicious = {n for n in config.read_commands if MUTATING_PREFIX.match(n)} - READ_NAME_ALLOWLIST
        assert not suspicious, f"{source}: read commands that look mutating: {sorted(suspicious)}"

def test_registration_without_kind_fails():
    with pytest.raises(TypeError):
        register_tool_for_dispatch(lambda conn_header: None, name="test_no_kind", title="T", description="D")

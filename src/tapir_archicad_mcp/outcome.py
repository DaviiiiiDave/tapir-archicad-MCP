"""Summaries of per-item results of write responses, so a partial failure is visible at the top of the response."""

from typing import Any

MAX_EXAMPLES = 3
MAX_CAUSES = 10
DEFAULT_RESULT_FIELDS = ("executionResults", "executionResult")


def summarize_execution_results(result: Any, fields: tuple[str, ...] = DEFAULT_RESULT_FIELDS) -> dict | None:
    """Count successes and failures of a write response and group the failures by cause.

    A failure is an item with ``success: false`` or with an ``error`` key. The items are read from the given
    top-level fields of the response: a list contributes its items, a single object counts as one item.
    Examples are indices into the items of all fields, concatenated in the order of ``fields``.
    Only the ``MAX_CAUSES`` most frequent causes are listed; ``otherCauses`` counts the rest.
    ``conflicts`` is the number of entries in a non-empty top-level ``conflicts`` list (Teamwork reservations).
    Returns None when none of the fields is a list or object in the response.
    """
    if not isinstance(result, dict):
        return None
    lists = [
        value if isinstance(value, list) else [value]
        for field in fields
        if isinstance(value := result.get(field), (list, dict))
    ]
    if not lists:
        return None

    items = [item for items_of_field in lists for item in items_of_field]
    causes: dict[tuple, dict] = {}
    failed = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not (item.get("success") is False or "error" in item):
            continue
        failed += 1
        error = item.get("error")
        if isinstance(error, dict):
            key = (error.get("code"), error.get("message"))
        else:
            key = (None, None if error is None else str(error))
        cause = causes.setdefault(key, {"code": key[0], "message": key[1], "count": 0, "examples": []})
        cause["count"] += 1
        if len(cause["examples"]) < MAX_EXAMPLES:
            cause["examples"].append(index)

    ranked = sorted(causes.values(), key=lambda cause: cause["count"], reverse=True)
    summary: dict[str, Any] = {
        "requested": len(items),
        "succeeded": len(items) - failed,
        "failed": failed,
        "failuresByCause": ranked[:MAX_CAUSES],
    }
    if len(ranked) > MAX_CAUSES:
        summary["otherCauses"] = {
            "causes": len(ranked) - MAX_CAUSES,
            "count": sum(cause["count"] for cause in ranked[MAX_CAUSES:]),
        }
    conflicts = result.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        summary["conflicts"] = len(conflicts)
    return summary


# Fields are those of the Tapir result models.
# Changing commands that report per-item errors in these result lists instead of `executionResults`
# (items are an id or an `error` object). Derived from the result models in multiconn_archicad.
RESULT_LIST_FIELDS: dict[str, tuple[str, ...]] = {
    "ChangeDrawingLink": ("elements",),
    "CloneProjectMapItemToViewMap": ("navigatorItems",),
    "ConnectMEPElements": ("connectionResults",),
    "CreateArcs": ("elements",),
    "CreateAssociativeDimensions": ("elements",),
    "CreateAssociativeDimensionsOnSection": ("elements",),
    "CreateBeams": ("elements",),
    "CreateBuildingMaterials": ("attributeIds",),
    "CreateCircles": ("elements",),
    "CreateColumns": ("elements",),
    "CreateComposites": ("attributeIds",),
    "CreateDesignOptionCombinations": ("designOptionCombinationIdsOrErrors",),
    "CreateDesignOptions": ("designOptionIdsOrErrors",),
    "CreateDetails": ("databases",),
    "CreateDoors": ("elements",),
    "CreateDrawings": ("elements",),
    "CreateFills": ("attributeIds",),
    "CreateGroups": ("groupGuids",),
    "CreateHatches": ("elements",),
    "CreateHotlinkInstances": ("elements",),
    "CreateHotlinkNodes": ("hotlinkNodes",),
    "CreateHotspots": ("elements",),
    "CreateInteriorElevations": ("elements",),
    "CreateKeynoteFolders": ("keynoteFolderIdsOrErrors",),
    "CreateKeynoteItems": ("keynoteItemIdsOrErrors",),
    "CreateKeynoteLabels": ("elements",),
    "CreateLabels": ("elements",),
    "CreateLamps": ("elements",),
    "CreateLayerCombinations": ("attributeIds",),
    "CreateLayers": ("attributeIds",),
    "CreateLayout": ("databases",),
    "CreateLayoutSubset": ("navigatorItems",),
    "CreateLineElements": ("elements",),
    "CreateLines": ("attributeIds",),
    "CreateMEPElements": ("elements",),
    "CreateMEPRoutingElements": ("elements",),
    "CreateMEPSystems": ("attributeIds",),
    "CreateMeshes": ("elements",),
    "CreateMorphs": ("elements",),
    "CreateObjects": ("elements",),
    "CreateOpenings": ("elements",),
    "CreatePenTables": ("attributeIds",),
    "CreatePolylines": ("elements",),
    "CreateProfiles": ("attributeIds",),
    "CreatePropertyDefinitions": ("propertyIds",),
    "CreateRoofs": ("elements",),
    "CreateSections": ("elements",),
    "CreateSlabs": ("elements",),
    "CreateSplines": ("elements",),
    "CreateStairs": ("elements",),
    "CreateSurfaces": ("attributeIds",),
    "CreateTexts": ("elements",),
    "CreateViewsInViewMap": ("navigatorItems",),
    "CreateWallThicknessDimensions": ("elements",),
    "CreateWalls": ("elements",),
    "CreateWindows": ("elements",),
    "CreateWorksheets": ("databases",),
    "CreateZoneCategories": ("attributeIds",),
    "CreateZones": ("elements",),
}


# Changing commands whose result has no per-item results to summarise, by title. A bare success/failure result
# (`SuccessfulExecutionResult | FailedExecutionResult` as the whole result) never reaches the outcome: Tapir
# raises TapirCommandError for a failure, so a returned result is a success. A title can be a Tapir and an
# official command; an entry that is also in RESULT_LIST_FIELDS covers only the official one.
_BARE = "bare success result; a failure raises TapirCommandError"
NO_ITEM_RESULTS: dict[str, str] = {
    "AddCommentToIssue": _BARE,
    "AddLibraries": _BARE,
    "AttachElementsToIssue": _BARE,
    "ChangeWindow": _BARE,
    "CloseProject": _BARE,
    "CloneProjectMapItemToViewMap": "official command returns only the created item id",
    "CreateIssue": "returns only the created issue id",
    "CreateProjectInfoFields": "returns only the created fields, no error items",
    "CreatePropertyGroups": "returns only the created group ids, no error items",
    "CreateLayout": "official command returns only the created item id",
    "CreateLayoutSubset": "official command returns only the created subset id",
    "CreateViewMapFolder": "returns only the created folder id",
    "DeleteIssue": _BARE,
    "DetachElementsFromIssue": _BARE,
    "ExportFavorites": "empty result",
    "ExportIssuesToBCF": _BARE,
    "IFCFileOperation": _BARE,
    "ImportFavorites": "returns only the name of the first conflict",
    "ImportIssuesFromBCF": _BARE,
    "LockElements": _BARE,
    "MoveAttributesAndFolders": "no result model in the API",
    "MoveNavigatorItem": "bare success result in Tapir; no result model in the official API",
    "OpenProject": _BARE,
    "PrintView": _BARE,
    "PublishPublisherSet": "no result model in the API",
    "ReleaseElements": _BARE,
    "ReleaseHotlinkCacheManagement": _BARE,
    "ReleaseLockableObjectSet": _BARE,
    "ReloadLibraries": _BARE,
    "RemoveElementNotificationClient": _BARE,
    "RenameNavigatorItem": _BARE,
    "SaveAsModuleFile": _BARE,
    "SaveProject": _BARE,
    "SaveProjectAsArchive": _BARE,
    "Set3DCutPlanes": _BARE,
    "SetElementNotificationClient": _BARE,
    "SetGeoLocation": _BARE,
    "SetLayoutSettings": "no result model in the API",
    "SetLibraries": _BARE,
    "SetProjectInfoField": "no result model in the API",
    "SetStories": _BARE,
    "ShowAlert": "returns only the clicked button",
    "TeamworkReceive": _BARE,
    "TeamworkSend": _BARE,
    "TrimElements": _BARE,
    "UnlockElements": _BARE,
    "UpdateDrawings": _BARE,
    "UpdateZones": _BARE,
}

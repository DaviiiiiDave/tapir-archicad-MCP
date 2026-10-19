"""Summaries of per-element execution results, so a partial write is never reported as plain success."""

from typing import Any

MAX_EXAMPLES = 3
DEFAULT_RESULT_FIELDS = ("executionResults",)


def summarize_execution_results(result: Any, fields: tuple[str, ...] = DEFAULT_RESULT_FIELDS) -> dict | None:
    """Count successes and failures of a write response and group the failures by cause.

    A failure is an item with ``success: false`` or with an ``error`` key. The items are read from the given
    top-level list fields of the response. Examples are positions in those lists, in field order.
    Returns None when none of the fields is a list in the response.
    """
    if not isinstance(result, dict):
        return None
    lists = [result[field] for field in fields if isinstance(result.get(field), list)]
    if not lists:
        return None

    items = [item for items_of_field in lists for item in items_of_field]
    causes: dict[tuple, dict] = {}
    failed = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not (item.get("success") is False or "error" in item):
            continue
        failed += 1
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        key = (error.get("code"), error.get("message"))
        cause = causes.setdefault(key, {"code": key[0], "message": key[1], "count": 0, "examples": []})
        cause["count"] += 1
        if len(cause["examples"]) < MAX_EXAMPLES:
            cause["examples"].append(index)

    return {
        "requested": len(items),
        "succeeded": len(items) - failed,
        "failed": failed,
        "failuresByCause": sorted(causes.values(), key=lambda cause: cause["count"], reverse=True),
    }


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

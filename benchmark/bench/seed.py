"""Seed the benchmark fixtures on the Benchmark instance and record their ground truth in manifests.

Facts this relies on were measured on AC29 (bench.toml [oracle.german], ledger Task 4/5):
new walls and doors start userUndefined and carry a classification inside the fire property's availability;
removing the classification makes the property notAvailable; curtain-wall panels are written through Tapir only;
writes to locked-layer and hotlinked walls fail with the same error in both APIs.
"""

import collections
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from bench.archicad import BenchArchicad
from bench.config import BenchConfig
from bench.oracle import PropertyState, details, door_like_elements, elements_of_type, property_id, snapshot_property

LAYER_T4_OPEN = "BENCH T4 open"
LAYER_T4_LOCKED = "BENCH T4 locked"
LAYER_T5 = "BENCH T5"
LAYER_MODULE = "BENCH Module"
LAYER_SCALE = "BENCH Scale"
SMALL_WALL_THICKNESS = 0.20  # below T2's 0.24 m rule, so T4/T5 walls never fall into T2's rule set
T2_MIN_THICKNESS = 0.24


def wall_grid(
    count: int,
    stories: list[int],
    thicknesses: list[tuple[float, int]],
    length: float,
    spacing: float,
    origin: tuple[float, float] = (0.0, 0.0),
    per_row: int = 50,
    gap: float = 0.5,
) -> list[dict]:
    """Straight Basic walls along x, stories assigned round-robin, thicknesses in the given blocks."""
    if sum(n for _, n in thicknesses) != count:
        raise ValueError(f"thickness counts sum to {sum(n for _, n in thicknesses)}, expected {count}")
    sequence = [thickness for thickness, n in thicknesses for _ in range(n)]
    walls = []
    for n, thickness in enumerate(sequence):
        slot = n // len(stories)
        x = origin[0] + (slot % per_row) * (length + gap)
        y = origin[1] + (slot // per_row) * spacing
        walls.append(
            {
                "begCoordinate": {"x": x, "y": y},
                "endCoordinate": {"x": x + length, "y": y},
                "floorIndex": stories[n % len(stories)],
                "structureType": "Basic",
                "thickness": thickness,
                "height": 3.0,
            }
        )
    return walls


def assign_states(ids: list[str], counts: dict[str, int]) -> dict[str, PropertyState]:
    """Sorted ids, states handed out in blocks in the order of counts."""
    if sum(counts.values()) != len(ids):
        raise ValueError(f"there are {len(ids)} ids but the counts sum to {sum(counts.values())}")
    states, ordered = {}, sorted(ids)
    start = 0
    for state, n in counts.items():
        for guid in ordered[start : start + n]:
            states[guid] = state
        start += n
    return states


def write_manifest(path: Path, manifest: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_states(expected: dict[str, str], actual: dict[str, tuple]) -> list[str]:
    problems = []
    for guid in sorted(set(expected) | set(actual)):
        want, found = expected.get(guid), actual.get(guid, (None,))[0]
        if want is None:
            problems.append(f"{guid}: not expected, found {found}")
        elif found is None:
            problems.append(f"{guid}: expected {want}, found nothing")
        elif want != found:
            problems.append(f"{guid}: expected {want}, found {found}")
    return problems


def _check(response: dict, what: str) -> None:
    failures = [r for r in response.get("executionResults", []) if not r.get("success")]
    if failures:
        raise RuntimeError(
            f"{what}: {len(failures)} of {len(response['executionResults'])} failed, first: {failures[0].get('error')}"
        )


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def set_states(
    ac, states: dict[str, str], prop: str, value: str, classification_system: dict, via: str, chunk: int = 1000
) -> None:
    """Produce each state: value / empty / undefined by writing the property, not_available by removing the
    classification. Through Tapir (curtain-wall panels) undefined cannot be written and is left as it starts."""
    if via == "official":
        call, set_command, classify_command = (
            ac.official,
            "API.SetPropertyValuesOfElements",
            "API.SetClassificationsOfElements",
        )
        payloads = {
            "value": {"type": "string", "status": "normal", "value": value},
            "empty": {"type": "string", "status": "normal", "value": ""},
            "undefined": {"type": "string", "status": "userUndefined"},
        }
    else:
        call, set_command, classify_command = ac.tapir, "SetPropertyValuesOfElements", "SetClassificationsOfElements"
        payloads = {"value": {"value": value}, "empty": {"value": ""}}
    writes = [(guid, payloads[state]) for guid, state in states.items() if state in payloads]
    for part in _chunks(writes, chunk):
        values = [{"elementId": {"guid": g}, "propertyId": {"guid": prop}, "propertyValue": p} for g, p in part]
        _check(call(set_command, {"elementPropertyValues": values}), set_command)
    unclassify = [guid for guid, state in states.items() if state == "not_available"]
    for part in _chunks(unclassify, chunk):
        items = [
            {"elementId": {"guid": g}, "classificationId": {"classificationSystemId": classification_system}}
            for g in part
        ]
        _check(call(classify_command, {"elementClassifications": items}), classify_command)


def _element_ids(response: dict) -> list[str]:
    errors = [e for e in response["elements"] if "error" in e]
    if errors:
        raise RuntimeError(f"{len(errors)} elements were not created, first: {errors[0]['error']}")
    return [e["elementId"]["guid"] for e in response["elements"]]


def create_walls(ac, walls: list[dict], batch: int = 500) -> list[str]:
    return [
        guid for part in _chunks(walls, batch) for guid in _element_ids(ac.tapir("CreateWalls", {"wallsData": part}))
    ]


def create_doors(ac, wall_ids: list[str], center_offset: float, batch: int = 500) -> list[str]:
    doors = [{"ownerWallId": {"guid": g}, "centerOffset": center_offset} for g in wall_ids]
    return [
        guid for part in _chunks(doors, batch) for guid in _element_ids(ac.tapir("CreateDoors", {"doorsData": part}))
    ]


def _layers(ac) -> list[dict]:
    attribute_ids = ac.official("API.GetAttributesByType", {"attributeType": "Layer"})["attributeIds"]
    return [layer for layer in ac.tapir("GetLayers", {"attributeIds": attribute_ids})["layers"] if "name" in layer]


def create_layers(ac, names: list[str]) -> dict[str, int]:
    """Create the missing layers in one call (Archicad may show one 'created attributes' dialog per call) with the
    intersection group the template's layers use, and return every name's index."""
    existing = _layers(ac)
    group = collections.Counter(layer.get("intersectionGroupNr") for layer in existing).most_common(1)[0][0]
    missing = [name for name in names if name not in {layer["name"] for layer in existing}]
    if missing:
        ac.tapir("CreateLayers", {"layerDataArray": [{"name": name, "intersectionGroupNr": group} for name in missing]})
    by_name = {layer["name"]: layer for layer in _layers(ac)}
    return {name: by_name[name]["index"] for name in names}


def lock_layer(ac, name: str) -> None:
    layer = next(layer for layer in _layers(ac) if layer["name"] == name)
    ac.tapir(
        "CreateLayers",
        {
            "layerDataArray": [
                {"name": name, "isLocked": True, "intersectionGroupNr": layer.get("intersectionGroupNr")}
            ],
            "overwriteExisting": True,
        },
    )
    if not next(layer for layer in _layers(ac) if layer["name"] == name).get("isLocked"):
        raise RuntimeError(f"layer {name} is not locked")


def move_to_layer(ac, ids: list[str], layer_index: int, batch: int = 500) -> None:
    for part in _chunks(ids, batch):
        items = [{"elementId": {"guid": g}, "details": {"layerIndex": layer_index}} for g in part]
        _check(ac.tapir("SetDetailsOfElements", {"elementsWithDetails": items}), "SetDetailsOfElements")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _open_fresh(ac: BenchArchicad, source: Path, target: Path) -> None:
    if ac.project_path() == target:
        raise RuntimeError(f"{target} is open in Archicad; open another project before seeding it again")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    ac.open_project(target)


def _write_errors(ac, guid: str, prop: str) -> dict:
    """The error each API returns for a write to guid. The write must fail; a success means the fixture is wrong."""
    official = ac.official(
        "API.SetPropertyValuesOfElements",
        {
            "elementPropertyValues": [
                {
                    "elementId": {"guid": guid},
                    "propertyId": {"guid": prop},
                    "propertyValue": {"type": "string", "status": "normal", "value": "REI 60"},
                }
            ]
        },
    )["executionResults"][0]
    tapir = ac.tapir(
        "SetPropertyValuesOfElements",
        {
            "elementPropertyValues": [
                {"elementId": {"guid": guid}, "propertyId": {"guid": prop}, "propertyValue": {"value": "REI 60"}}
            ]
        },
    )["executionResults"][0]
    if official.get("success") or tapir.get("success"):
        raise RuntimeError(f"a write to {guid} succeeded, but it must be refused")
    return {"official": official["error"], "tapir": tapir["error"]}


def _base_manifest(
    cfg: BenchConfig, name: str, fixture: Path, group: str, prop_name: str, prop: str, spec: dict
) -> dict:
    return {
        "name": name,
        "fixture": str(fixture),
        "property": {"group": group, "name": prop_name, "id": prop},
        "seed_spec": spec,
        "created_at": datetime.now(UTC).isoformat(),
    }


def seed_german(ac: BenchArchicad, cfg: BenchConfig) -> Path:
    spec, rule = cfg.fixtures["german"], cfg.oracle["german"]
    fixture = cfg.data_dir / "fixtures" / "german.pln"
    _open_fresh(ac, cfg.base_project, fixture)
    fire = property_id(ac, rule["fire_property_group"], rule["fire_property_name"])
    system = {"guid": rule["classification_system"]}
    layers = create_layers(ac, [LAYER_T4_OPEN, LAYER_T4_LOCKED, LAYER_T5, LAYER_MODULE])

    def small_walls(count, story, origin):
        return create_walls(
            ac, wall_grid(count, [story], [(SMALL_WALL_THICKNESS, count)], 3.0, 1.0, origin=origin, per_row=10)
        )

    t4_open = small_walls(spec["t4_open"], 0, (1000.0, 0.0))
    t4_locked = small_walls(spec["t4_locked"], 0, (1000.0, 20.0))
    module_source = small_walls(spec["t4_hotlinked"], 0, (1000.0, 40.0))
    t5 = small_walls(spec["t5_walls"], 1, (1000.0, 60.0))
    move_to_layer(ac, t4_open, layers[LAYER_T4_OPEN])
    move_to_layer(ac, t4_locked, layers[LAYER_T4_LOCKED])
    move_to_layer(ac, module_source, layers[LAYER_MODULE])
    move_to_layer(ac, t5, layers[LAYER_T5])
    set_states(
        ac, dict.fromkeys(t4_open + t4_locked + module_source, "value"), fire, spec["t4_value"], system, "official"
    )

    module = cfg.data_dir / "fixtures" / "bench-module.mod"
    ac.tapir(
        "SaveAsModuleFile",
        {"moduleFilePath": str(module), "elements": [{"elementId": {"guid": g}} for g in module_source]},
    )
    _check(
        ac.tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in module_source]}), "DeleteElements"
    )
    node = ac.tapir("CreateHotlinkNodes", {"hotlinkNodes": [{"sourceLocation": str(module)}]})["hotlinkNodes"][0]
    _element_ids(
        ac.tapir(
            "CreateHotlinkInstances",
            {
                "hotlinkInstances": [
                    {"hotlinkNodeId": node["hotlinkNodeId"], "origin": {"x": 0.0, "y": 0.0}, "floorIndex": 0}
                ]
            },
        )
    )
    walls = elements_of_type(ac, "Wall")
    hotlinked = [guid for guid, entry in details(ac, walls).items() if entry.get("hotlinkId")]
    if len(hotlinked) != spec["t4_hotlinked"]:
        raise RuntimeError(f"expected {spec['t4_hotlinked']} hotlinked walls, found {len(hotlinked)}")
    lock_layer(ac, LAYER_T4_LOCKED)

    panels = door_like_elements(ac, rule)
    half = len(panels) // 2
    panel_states = assign_states(panels, {"value": half, "not_available": len(panels) - half})
    set_states(ac, panel_states, fire, spec["panel_value"], system, "tapir")

    errors = {"locked_layer": _write_errors(ac, t4_locked[0], fire), "hotlink": _write_errors(ac, hotlinked[0], fire)}
    ac.save_project()
    manifest = _base_manifest(
        cfg, "german", fixture, rule["fire_property_group"], rule["fire_property_name"], fire, spec
    )
    manifest |= {
        "sha256": _sha256(fixture),
        "doors": panel_states,
        "t4_targets": sorted(t4_open + t4_locked + hotlinked),
        "t4_buckets": [
            {"cause": "locked_layer", "guids": sorted(t4_locked), **errors["locked_layer"]},
            {"cause": "hotlink", "guids": sorted(hotlinked), **errors["hotlink"]},
        ],
        "t4_value": spec["t4_value"],
        "t5_walls": sorted(t5),
    }
    return write_manifest(cfg.data_dir / "fixtures" / "german.manifest.json", manifest)


def seed_scale(ac: BenchArchicad, cfg: BenchConfig) -> Path:
    spec, rule = cfg.fixtures["scale"], cfg.oracle["german"]
    german = read_manifest(cfg.data_dir / "fixtures" / "german.manifest.json")
    fixture = cfg.data_dir / "fixtures" / "scale.pln"
    _open_fresh(ac, Path(german["fixture"]), fixture)
    fire = property_id(ac, rule["fire_property_group"], rule["fire_property_name"])
    system = {"guid": rule["classification_system"]}
    layers = create_layers(ac, [LAYER_SCALE])

    grid = wall_grid(
        spec["walls_thick"] + spec["walls_thin"],
        [-1, 0, 1],
        [(spec["thick"], spec["walls_thick"]), (spec["thin"], spec["walls_thin"])],
        3.0,
        1.0,
        origin=(2000.0, 0.0),
    )
    walls = create_walls(ac, grid, batch=spec["batch"])
    move_to_layer(ac, walls, layers[LAYER_SCALE], batch=spec["batch"])
    thickness = dict(zip(walls, (w["thickness"] for w in grid), strict=True))
    wall_states = assign_states(walls, spec["wall_states"])
    set_states(ac, wall_states, fire, spec["wall_value"], system, "official")

    thin = sorted(guid for guid in walls if thickness[guid] < T2_MIN_THICKNESS)[: spec["doors"]]
    doors = create_doors(ac, thin, center_offset=1.5, batch=spec["batch"])
    door_states = assign_states(doors, spec["door_states"])
    set_states(ac, door_states, fire, spec["door_value"], system, "official")
    ac.save_project()

    rule_set = sorted(guid for guid in walls if thickness[guid] >= T2_MIN_THICKNESS)
    other = sorted(set(elements_of_type(ac, "Wall")) - set(rule_set))
    manifest = _base_manifest(
        cfg, "scale", fixture, rule["fire_property_group"], rule["fire_property_name"], fire, spec
    )
    manifest |= {
        "sha256": _sha256(fixture),
        "doors": german["doors"] | door_states,
        "t2_rule_set": rule_set,
        "t2_other": other,
        "t2_states": wall_states,
    }
    return write_manifest(cfg.data_dir / "fixtures" / "scale.manifest.json", manifest)


def verify(ac: BenchArchicad, cfg: BenchConfig, manifest_path: Path) -> list[str]:
    """Open the fixture fresh and re-derive its ground truth; return every mismatch with the manifest."""
    manifest = read_manifest(manifest_path)
    ac.open_project(Path(manifest["fixture"]))
    problems = []
    if _sha256(Path(manifest["fixture"])) != manifest["sha256"]:
        problems.append("fixture file changed since seeding")
    prop = manifest["property"]["id"]
    rule = cfg.oracle["german"]
    doors = door_like_elements(ac, rule)
    problems += [f"door {p}" for p in compare_states(manifest["doors"], snapshot_property(ac, doors, prop))]

    walls = elements_of_type(ac, "Wall")
    info = details(ac, walls)
    if "t4_targets" in manifest:
        locked_layers = {layer["index"] for layer in _layers(ac) if layer.get("isLocked")}
        on_locked = sorted(g for g, d in info.items() if d["layerIndex"] in locked_layers and not d.get("hotlinkId"))
        hotlinked = sorted(g for g, d in info.items() if d.get("hotlinkId"))
        buckets = {b["cause"]: b["guids"] for b in manifest["t4_buckets"]}
        if on_locked != buckets["locked_layer"]:
            problems.append(f"locked-layer walls: manifest {len(buckets['locked_layer'])}, project {len(on_locked)}")
        if hotlinked != buckets["hotlink"]:
            problems.append(f"hotlinked walls: manifest {len(buckets['hotlink'])}, project {len(hotlinked)}")
        eg = sorted(g for g, d in info.items() if d["floorIndex"] == 0)
        if eg != manifest["t4_targets"]:
            problems.append(f"walls on EG: manifest {len(manifest['t4_targets'])}, project {len(eg)}")
    if "t2_rule_set" in manifest:
        thick = sorted(g for g, d in info.items() if (d["details"].get("begThickness") or 0) >= T2_MIN_THICKNESS - 1e-9)
        if thick != manifest["t2_rule_set"]:
            problems.append(
                f"walls >= {T2_MIN_THICKNESS} m: manifest {len(manifest['t2_rule_set'])}, project {len(thick)}"
            )
        states = snapshot_property(ac, list(manifest["t2_states"]), prop)
        problems += [f"wall {p}" for p in compare_states(manifest["t2_states"], states)]
    return problems

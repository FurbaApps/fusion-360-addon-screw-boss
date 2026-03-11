from dataclasses import dataclass
from typing import Iterable, List

import adsk.core
import adsk.fusion

from .models import ScrewBossPreset


MM_TO_CM = 0.1


@dataclass
class BossGenerationContext:
    component: adsk.fusion.Component
    sketch: adsk.fusion.Sketch
    preset: ScrewBossPreset
    boss_height_mm: float


@dataclass
class BossGenerationResult:
    bodies: List[adsk.fusion.BRepBody]
    sketches: List[adsk.fusion.Sketch]


def _mm_to_cm(value_mm: float) -> float:
    return value_mm * MM_TO_CM


def _configure_helper_sketch(sketch: adsk.fusion.Sketch, name: str):
    if not sketch:
        return

    if hasattr(sketch, 'name'):
        sketch.name = name

    # Helper sketches are construction artifacts and should not clutter the browser.
    if hasattr(sketch, 'isLightBulbOn'):
        sketch.isLightBulbOn = False
    elif hasattr(sketch, 'isVisible'):
        sketch.isVisible = False


def _profile_for_circle(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle) -> adsk.fusion.Profile:
    for profile in sketch.profiles:
        for loop in profile.profileLoops:
            for curve in loop.profileCurves:
                if curve.sketchEntity == circle:
                    return profile
    raise RuntimeError('Could not resolve profile from sketch circle.')


def _create_extrude(
    component: adsk.fusion.Component,
    profile,
    distance_cm: float,
    operation: adsk.fusion.FeatureOperations,
    direction: adsk.fusion.ExtentDirections = adsk.fusion.ExtentDirections.PositiveExtentDirection,
    participant_body: adsk.fusion.BRepBody = None,
) -> adsk.fusion.ExtrudeFeature:
    extrudes = component.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, operation)

    if participant_body and hasattr(ext_input, 'participantBodies'):
        try:
            # Newer bindings expect a vector-like Python sequence.
            ext_input.participantBodies = [participant_body]
        except TypeError:
            # Fallback for builds that still accept ObjectCollection.
            participants = adsk.core.ObjectCollection.create()
            participants.add(participant_body)
            ext_input.participantBodies = participants

    distance_input = adsk.core.ValueInput.createByReal(distance_cm)
    extent = adsk.fusion.DistanceExtentDefinition.create(distance_input)
    ext_input.setOneSideExtent(extent, direction)
    return extrudes.add(ext_input)


def _circle_center_on_face(face: adsk.fusion.BRepFace, target_radius_cm: float) -> adsk.core.Point3D:
    for loop in face.loops:
        for coedge in loop.coEdges:
            circle = adsk.core.Circle3D.cast(coedge.edge.geometry)
            if not circle:
                continue
            if abs(circle.radius - target_radius_cm) <= 1e-6:
                return circle.center
    raise RuntimeError('Could not resolve circular top-face center for hole placement.')


def _circle_edge_on_body_near_point(
    body: adsk.fusion.BRepBody,
    target_radius_cm: float,
    near_point: adsk.core.Point3D,
) -> adsk.fusion.BRepEdge:
    best_edge = None
    best_distance = None

    for edge in body.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if not circle:
            continue
        if abs(circle.radius - target_radius_cm) > 1e-6:
            continue

        distance = circle.center.distanceTo(near_point)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_edge = edge

    if not best_edge:
        raise RuntimeError('Could not resolve base circular edge for fillet.')

    return best_edge


def _add_base_fillet(component: adsk.fusion.Component, edge: adsk.fusion.BRepEdge, radius_cm: float):
    fillets = component.features.filletFeatures
    fillet_input = fillets.createInput()
    edge_collection = adsk.core.ObjectCollection.create()
    edge_collection.add(edge)
    fillet_radius = adsk.core.ValueInput.createByReal(radius_cm)
    fillet_input.addConstantRadiusEdgeSet(edge_collection, fillet_radius, True)
    fillets.add(fillet_input)


def _add_base_fillet_edges(component: adsk.fusion.Component, edges: adsk.core.ObjectCollection, radius_cm: float):
    if edges.count == 0:
        return

    fillets = component.features.filletFeatures
    fillet_input = fillets.createInput()
    fillet_radius = adsk.core.ValueInput.createByReal(radius_cm)
    fillet_input.addConstantRadiusEdgeSet(edges, fillet_radius, True)
    fillets.add(fillet_input)


def _distance_point_to_bbox(point: adsk.core.Point3D, bbox: adsk.core.BoundingBox3D) -> float:
    dx = max(bbox.minPoint.x - point.x, 0.0, point.x - bbox.maxPoint.x)
    dy = max(bbox.minPoint.y - point.y, 0.0, point.y - bbox.maxPoint.y)
    dz = max(bbox.minPoint.z - point.z, 0.0, point.z - bbox.maxPoint.z)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _resolve_target_body_near_point(component: adsk.fusion.Component, near_point: adsk.core.Point3D) -> adsk.fusion.BRepBody:
    best_body = None
    best_distance = None

    for body in component.bRepBodies:
        if not body.isSolid:
            continue

        distance = _distance_point_to_bbox(near_point, body.boundingBox)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_body = body

    if not best_body:
        raise RuntimeError('No solid target body found near selected boss point.')

    return best_body


def _resolve_top_face_on_body(
    body: adsk.fusion.BRepBody,
    base_point: adsk.core.Point3D,
    target_radius_cm: float,
    boss_height_cm: float,
) -> adsk.fusion.BRepFace:
    refs = []

    for face in body.faces:
        if not adsk.core.Plane.cast(face.geometry):
            continue

        center = None
        for loop in face.loops:
            for coedge in loop.coEdges:
                circle = adsk.core.Circle3D.cast(coedge.edge.geometry)
                if not circle:
                    continue
                if abs(circle.radius - target_radius_cm) <= 1e-6:
                    center = circle.center
                    break
            if center:
                break

        if center:
            refs.append((face, center.distanceTo(base_point)))

    if not refs:
        raise RuntimeError('Could not resolve boss circular top face on joined body.')

    # Choose the face whose center distance best matches expected boss height.
    # This avoids selecting a neighboring boss face when many bosses exist.
    refs.sort(key=lambda item: abs(item[1] - boss_height_cm))
    return refs[0][0]


def _create_counterbore_input(
    hole_features: adsk.fusion.HoleFeatures,
    main_diameter_cm: float,
    relief_diameter_cm: float,
    relief_depth_cm: float,
):
    main_diam = adsk.core.ValueInput.createByReal(main_diameter_cm)
    relief_diam = adsk.core.ValueInput.createByReal(relief_diameter_cm)
    relief_depth = adsk.core.ValueInput.createByReal(relief_depth_cm)

    try:
        return hole_features.createCounterboreInput(main_diam, relief_diam, relief_depth)
    except TypeError:
        # Some API builds include tip angle in this constructor.
        tip = adsk.core.ValueInput.createByString('180 deg')
        return hole_features.createCounterboreInput(main_diam, relief_diam, relief_depth, tip)


def _set_hole_depth(hole_input, depth_cm: float):
    depth_input = adsk.core.ValueInput.createByReal(depth_cm)
    if hasattr(hole_input, 'setDistanceExtent'):
        try:
            hole_input.setDistanceExtent(depth_input)
            return
        except TypeError:
            hole_input.setDistanceExtent(depth_input, False)


def _set_hole_position(hole_input, sketch_point: adsk.fusion.SketchPoint):
    if hasattr(hole_input, 'setPositionBySketchPoint'):
        try:
            hole_input.setPositionBySketchPoint(sketch_point)
            return
        except TypeError:
            # Older variants accept (point, orientationEntity).
            pass

    if hasattr(hole_input, 'setPositionByPoint'):
        hole_input.setPositionByPoint(sketch_point.worldGeometry)
        return

    raise RuntimeError('Hole input does not support setPositionBySketchPoint in this API version.')


def _set_flat_drill_point(hole_input):
    # In Fusion, a flat drill point corresponds to 180 degree tip angle.
    if hasattr(hole_input, 'tipAngle'):
        hole_input.tipAngle = adsk.core.ValueInput.createByString('180 deg')


def _set_simple_tap_type(hole_input):
    # Only apply if this API build exposes tap-type controls.
    hole_tap_types = getattr(adsk.fusion, 'HoleTapTypes', None)
    simple_tap = getattr(hole_tap_types, 'SimpleHoleTapType', None) if hole_tap_types else None
    if simple_tap is not None and hasattr(hole_input, 'tapType'):
        hole_input.tapType = simple_tap


def _create_counterbore_hole(
    component: adsk.fusion.Component,
    top_face: adsk.fusion.BRepFace,
    hole_center_world: adsk.core.Point3D,
    main_diameter_cm: float,
    main_depth_cm: float,
    relief_diameter_cm: float,
    relief_depth_cm: float,
    created_sketches: List[adsk.fusion.Sketch] = None,
):
    hole_features = component.features.holeFeatures
    hole_input = _create_counterbore_input(
        hole_features,
        main_diameter_cm,
        relief_diameter_cm,
        relief_depth_cm,
    )

    placement_sketch = component.sketches.add(top_face)
    _configure_helper_sketch(placement_sketch, 'Screw Boss - Hole Positions')
    if created_sketches is not None:
        created_sketches.append(placement_sketch)
    center_on_face = placement_sketch.modelToSketchSpace(hole_center_world)
    sketch_point = placement_sketch.sketchPoints.add(center_on_face)

    _set_hole_position(hole_input, sketch_point)
    _set_hole_depth(hole_input, main_depth_cm)
    _set_flat_drill_point(hole_input)
    _set_simple_tap_type(hole_input)

    hole_features.add(hole_input)


def _create_counterbore_holes_batch(
    component: adsk.fusion.Component,
    top_face: adsk.fusion.BRepFace,
    hole_centers_world: List[adsk.core.Point3D],
    main_diameter_cm: float,
    main_depth_cm: float,
    relief_diameter_cm: float,
    relief_depth_cm: float,
    created_sketches: List[adsk.fusion.Sketch] = None,
):
    hole_features = component.features.holeFeatures
    hole_input = _create_counterbore_input(
        hole_features,
        main_diameter_cm,
        relief_diameter_cm,
        relief_depth_cm,
    )

    placement_sketch = component.sketches.add(top_face)
    _configure_helper_sketch(placement_sketch, 'Screw Boss - Hole Positions')
    if created_sketches is not None:
        created_sketches.append(placement_sketch)
    sketch_points = []
    for center in hole_centers_world:
        center_on_face = placement_sketch.modelToSketchSpace(center)
        sketch_points.append(placement_sketch.sketchPoints.add(center_on_face))

    if hasattr(hole_input, 'setPositionBySketchPoints'):
        try:
            hole_input.setPositionBySketchPoints(sketch_points)
        except TypeError:
            points_collection = adsk.core.ObjectCollection.create()
            for point in sketch_points:
                points_collection.add(point)
            hole_input.setPositionBySketchPoints(points_collection)
    else:
        # API fallback: use one hole feature per point if batch positioning is unavailable.
        for center in hole_centers_world:
            _create_counterbore_hole(
                component,
                top_face,
                center,
                main_diameter_cm,
                main_depth_cm,
                relief_diameter_cm,
                relief_depth_cm,
                created_sketches,
            )
        return

    _set_hole_depth(hole_input, main_depth_cm)
    _set_flat_drill_point(hole_input)
    _set_simple_tap_type(hole_input)
    hole_features.add(hole_input)


def generate_bosses(context: BossGenerationContext, points: Iterable[adsk.fusion.SketchPoint]) -> BossGenerationResult:
    component = context.component
    source_sketch = context.sketch
    preset = context.preset

    height_cm = _mm_to_cm(context.boss_height_mm)
    outer_radius_cm = _mm_to_cm(preset.outer_radius_mm)
    main_diameter_cm = _mm_to_cm(preset.main_hole_diameter_mm)
    main_depth_cm = _mm_to_cm(preset.main_hole_depth_from_top_mm)
    relief_diameter_cm = _mm_to_cm(preset.top_relief_diameter_mm)
    relief_depth_cm = _mm_to_cm(preset.top_relief_depth_mm)
    fillet_radius_cm = _mm_to_cm(preset.base_fillet_mm)

    point_list = list(points)
    if not point_list:
        return BossGenerationResult(bodies=[], sketches=[])

    created_sketches: List[adsk.fusion.Sketch] = []

    center_world_points = [source_sketch.sketchToModelSpace(point.geometry) for point in point_list]

    # 1) One sketch with all boss circles.
    boss_sketch = component.sketches.add(source_sketch.referencePlane)
    _configure_helper_sketch(boss_sketch, 'Screw Boss - Profiles')
    created_sketches.append(boss_sketch)
    circles = []
    for center_world in center_world_points:
        center_on_boss_sketch = boss_sketch.modelToSketchSpace(center_world)
        circle = boss_sketch.sketchCurves.sketchCircles.addByCenterRadius(center_on_boss_sketch, outer_radius_cm)
        circles.append(circle)

    # Resolve fresh profile references after the sketch is fully populated.
    profiles = adsk.core.ObjectCollection.create()
    for circle in circles:
        profile = _profile_for_circle(boss_sketch, circle)
        profiles.add(profile)

    host_body = _resolve_target_body_near_point(component, center_world_points[0])

    # 2) One join extrude for all boss profiles.
    _create_extrude(
        component,
        profiles,
        height_cm,
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        participant_body=host_body,
    )

    # Resolve all top centers and base edges per selected point.
    top_centers = []
    base_edges = adsk.core.ObjectCollection.create()
    edge_tokens = set()

    for center_world in center_world_points:
        body = _resolve_target_body_near_point(component, center_world)
        top_face = _resolve_top_face_on_body(body, center_world, outer_radius_cm, height_cm)
        top_centers.append(_circle_center_on_face(top_face, outer_radius_cm))

        edge = _circle_edge_on_body_near_point(body, outer_radius_cm, center_world)
        token = edge.entityToken if hasattr(edge, 'entityToken') else None
        if token is None or token not in edge_tokens:
            base_edges.add(edge)
            if token is not None:
                edge_tokens.add(token)

    # 3) One hole feature for all centers (when API supports setPositionBySketchPoints).
    hole_top_face = _resolve_top_face_on_body(host_body, center_world_points[0], outer_radius_cm, height_cm)
    _create_counterbore_holes_batch(
        component,
        hole_top_face,
        top_centers,
        main_diameter_cm,
        main_depth_cm,
        relief_diameter_cm,
        relief_depth_cm,
        created_sketches,
    )

    # 4) One fillet feature with all base edges.
    _add_base_fillet_edges(component, base_edges, fillet_radius_cm)

    return BossGenerationResult(bodies=[host_body], sketches=created_sketches)

#!/usr/bin/env python3
"""Generate the Brufik N3 mobile-base enclosure and mounting parts.

The design is intentionally parametric.  The seller drawing is the only
mechanical authority currently available, so dimensions that must be checked
against the delivered chassis are grouped in ``N3`` below.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cadquery as cq
from cadquery import exporters

try:
    import cairosvg
except (ImportError, OSError):  # PNG previews are optional; CAD exports do not depend on Cairo.
    cairosvg = None

try:
    from vtkmodules.vtkIOGeometry import vtkSTLReader
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.vtkRenderingCore import (
        vtkActor,
        vtkPolyDataMapper,
        vtkRenderer,
        vtkRenderWindow,
        vtkWindowToImageFilter,
    )
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - registers the renderer

    VTK_AVAILABLE = True
except ImportError:
    VTK_AVAILABLE = False


OUT = Path(__file__).resolve().parent
EXPORT = OUT / "export"
PREVIEW = OUT / "preview"


@dataclass(frozen=True)
class N3Dimensions:
    # Seller drawing / listing.
    complete_width: float = 160.0
    complete_depth: float = 150.0
    complete_height: float = 65.0
    wheel_diameter: float = 50.0
    controller_hole_x: float = 58.0
    controller_hole_y: float = 49.0
    acrylic_thickness: float = 3.0
    deck_spacing: float = 30.0

    # Parametric assumptions to be confirmed when the kit arrives.
    wheel_center_radius: float = 57.0
    wheel_tangent_clearance: float = 59.0
    wheel_radial_clearance: float = 37.0
    mounting_slot_length: float = 7.0
    mounting_slot_width: float = 3.6


@dataclass(frozen=True)
class BrufikDimensions:
    # Derived from the printable model; clearance is included.
    base_recess_x: float = 54.0
    base_recess_y: float = 49.0
    cradle_x: float = 74.0
    cradle_y: float = 66.0


N3 = N3Dimensions()
BRUFIK = BrufikDimensions()


def rounded_box(x: float, y: float, z: float, radius: float) -> cq.Workplane:
    """A centered rectangular prism with filleted vertical edges."""
    return (
        cq.Workplane("XY")
        .box(x, y, z, centered=(True, True, False))
        .edges("|Z")
        .fillet(radius)
    )


def mounting_slot(length: float, width: float, height: float) -> cq.Workplane:
    """A printable M3 tolerance slot, centered on the origin."""
    return cq.Workplane("XY").slot2D(length, width).extrude(height)


def mount_points() -> list[tuple[float, float]]:
    hx = N3.controller_hole_x / 2
    hy = N3.controller_hole_y / 2
    return [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]


def rounded_triangle_solid(
    center_radius: float,
    lobe_radius: float,
    height: float,
) -> cq.Workplane:
    """Rounded-triangle prism made from three capsules.

    CadQuery 2.5 does not expose a 2-D hull operation, so the equivalent hull
    is built from circles at the vertices and rectangular edge bridges.
    """
    points: list[tuple[float, float]] = []
    for angle_deg in (90.0, 210.0, 330.0):
        angle = math.radians(angle_deg)
        points.append((center_radius * math.cos(angle), center_radius * math.sin(angle)))

    solid: cq.Workplane | None = None
    for x, y in points:
        disk = cq.Workplane("XY").center(x, y).circle(lobe_radius).extrude(height)
        solid = disk if solid is None else solid.union(disk)

    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        angle_deg = math.degrees(math.atan2(dy, dx))
        bridge = (
            cq.Workplane("XY")
            .box(length, 2 * lobe_radius, height, centered=(True, True, False))
            .rotate((0, 0, 0), (0, 0, 1), angle_deg)
            .translate(((x1 + x2) / 2, (y1 + y2) / 2, 0))
        )
        solid = solid.union(bridge)

    assert solid is not None
    return solid


def wheel_cutouts(height: float) -> cq.Workplane:
    result: cq.Workplane | None = None
    for angle_deg in (90.0, 210.0, 330.0):
        angle = math.radians(angle_deg)
        px = N3.wheel_center_radius * math.cos(angle)
        py = N3.wheel_center_radius * math.sin(angle)
        cut = (
            cq.Workplane("XY")
            .box(
                N3.wheel_radial_clearance,
                N3.wheel_tangent_clearance,
                height,
                centered=(True, True, False),
            )
            .rotate((0, 0, 0), (0, 0, 1), angle_deg)
            .translate((px, py, 0))
        )
        result = cut if result is None else result.union(cut)
    assert result is not None
    return result


def chassis_upper_cowl() -> cq.Workplane:
    """Low-profile upper cowl with three open wheel arches."""
    h = 18.0
    top = 2.4
    outer = rounded_triangle_solid(49.0, 24.0, h)
    inner = rounded_triangle_solid(46.0, 21.0, h - top)
    # Wheels remain below the upper deck on this chassis.  The wheel openings
    # therefore remove only the vertical skirt, leaving a continuous top skin.
    shell = outer.cut(inner).cut(wheel_cutouts(h - top))

    # The adapter, cowl and N3 top plate share the same four fasteners.
    for x, y in mount_points():
        slot = mounting_slot(
            N3.mounting_slot_length,
            N3.mounting_slot_width,
            h + 2.0,
        ).translate((x, y, -1.0))
        shell = shell.cut(slot)

    # Wiring pass-through and a rear service opening for the power switch.
    cable = cq.Workplane("XY").slot2D(22.0, 12.0).extrude(h + 2.0).translate((0, 0, -1))
    switch = (
        cq.Workplane("XY")
        .box(17.0, 22.0, 10.0, centered=(True, True, False))
        .translate((0, -57.0, 4.0))
    )
    return shell.cut(cable).cut(switch)


def brufik_cradle_adapter() -> cq.Workplane:
    """Sandwich plate locating the existing Brufik base without modifying it."""
    base_h = 4.0
    base = rounded_box(BRUFIK.cradle_x, BRUFIK.cradle_y, base_h, 7.0)

    # Shallow interrupted locating rim; the interruptions preserve screw access.
    outer = rounded_box(
        BRUFIK.base_recess_x + 6.0,
        BRUFIK.base_recess_y + 6.0,
        4.0,
        9.0,
    ).translate((0, 0, base_h))
    inner = rounded_box(
        BRUFIK.base_recess_x,
        BRUFIK.base_recess_y,
        4.5,
        7.0,
    ).translate((0, 0, base_h - 0.1))
    rim = outer.cut(inner)

    adapter = base.union(rim)
    for x, y in mount_points():
        adapter = adapter.cut(
            mounting_slot(
                N3.mounting_slot_length,
                N3.mounting_slot_width,
                10.0,
            ).translate((x, y, -1.0))
        )
        # Screwdriver/head clearance interrupts the rim locally.
        adapter = adapter.cut(
            cq.Workplane("XY").center(x, y).circle(4.3).extrude(10.0)
        )

    cable = cq.Workplane("XY").slot2D(20.0, 11.0).extrude(10.0)
    # Two optional 12 mm straps can be used instead of adhesive.
    straps = (
        cq.Workplane("XY")
        .pushPoints([(-23.0, 0), (23.0, 0)])
        .slot2D(15.0, 3.0, angle=90)
        .extrude(10.0)
    )
    return adapter.cut(cable).cut(straps)


def hole_fit_jig() -> cq.Workplane:
    """Fast print used before committing to the full cowl."""
    jig = rounded_box(68.0, 59.0, 1.6, 3.0)
    for x, y in mount_points():
        jig = jig.cut(
            mounting_slot(
                N3.mounting_slot_length,
                N3.mounting_slot_width,
                4.0,
            ).translate((x, y, -1.0))
        )
    return jig.cut(cq.Workplane("XY").circle(6.2).extrude(4.0).translate((0, 0, -1)))


def touch_electrode_holder() -> cq.Workplane:
    """Adhesive holder for a 24 x 16 mm copper-foil touch electrode."""
    plate = rounded_box(32.0, 24.0, 1.6, 4.0)
    recess = rounded_box(25.0, 17.0, 0.8, 3.0).translate((0, 0, 0.9))
    cable = (
        cq.Workplane("XY")
        .box(7.0, 5.0, 3.0, centered=(True, True, False))
        .translate((0, -11.5, 0))
    )
    tie_slots = (
        cq.Workplane("XY")
        .pushPoints([(-13.0, 0), (13.0, 0)])
        .slot2D(8.0, 2.2, angle=90)
        .extrude(3.0)
    )
    return plate.cut(recess).cut(cable).cut(tie_slots)


def assembly_preview() -> cq.Assembly:
    cowl = chassis_upper_cowl()
    cradle = brufik_cradle_adapter().translate((0, 0, 18.0))
    # Simplified Brufik base envelope for scale and collision review.
    robot_envelope = rounded_box(
        BRUFIK.base_recess_x - 1.0,
        BRUFIK.base_recess_y - 1.0,
        58.0,
        8.0,
    ).translate((0, 0, 25.0))
    assembly = cq.Assembly()
    assembly.add(cowl, name="N3 upper cowl", color=cq.Color(0.84, 0.86, 0.90))
    assembly.add(cradle, name="Brufik cradle", color=cq.Color(0.18, 0.20, 0.24))
    assembly.add(
        robot_envelope,
        name="Brufik clearance envelope",
        color=cq.Color(0.95, 0.95, 0.95, 0.35),
    )
    return assembly


def export_part(name: str, part: cq.Workplane) -> dict[str, list[float]]:
    stl_path = EXPORT / f"{name}.stl"
    step_path = EXPORT / f"{name}.step"
    exporters.export(part, str(stl_path), tolerance=0.08, angularTolerance=0.15)
    exporters.export(part, str(step_path))
    box = part.val().BoundingBox()
    return {
        "size_mm": [round(box.xlen, 3), round(box.ylen, 3), round(box.zlen, 3)],
        "min_mm": [round(box.xmin, 3), round(box.ymin, 3), round(box.zmin, 3)],
        "max_mm": [round(box.xmax, 3), round(box.ymax, 3), round(box.zmax, 3)],
    }


def render_stl(stl_path: Path, png_path: Path) -> None:
    if not VTK_AVAILABLE:
        return
    reader = vtkSTLReader()
    reader.SetFileName(str(stl_path))
    mapper = vtkPolyDataMapper()
    mapper.SetInputConnection(reader.GetOutputPort())
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.82, 0.85, 0.90)
    actor.GetProperty().SetSpecular(0.25)
    actor.GetProperty().SetSpecularPower(18.0)

    renderer = vtkRenderer()
    renderer.AddActor(actor)
    renderer.SetBackground(0.98, 0.98, 0.98)
    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(1200, 900)
    window.AddRenderer(renderer)

    renderer.ResetCamera()
    camera = renderer.GetActiveCamera()
    camera.Azimuth(38)
    camera.Elevation(30)
    camera.SetParallelProjection(True)
    renderer.ResetCameraClippingRange()
    window.Render()

    image_filter = vtkWindowToImageFilter()
    image_filter.SetInput(window)
    image_filter.SetInputBufferTypeToRGBA()
    image_filter.ReadFrontBufferOff()
    image_filter.Update()
    writer = vtkPNGWriter()
    writer.SetFileName(str(png_path))
    writer.SetInputConnection(image_filter.GetOutputPort())
    writer.Write()


def main() -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)

    parts = {
        "N3_upper_cowl_v0_1": chassis_upper_cowl(),
        "Brufik_to_N3_adapter_v0_1": brufik_cradle_adapter(),
        "N3_58x49_fit_jig_v0_1": hole_fit_jig(),
        "Head_touch_electrode_holder_v0_1": touch_electrode_holder(),
    }
    measurements = {name: export_part(name, part) for name, part in parts.items()}
    for name in parts:
        render_stl(
            EXPORT / f"{name}.stl",
            PREVIEW / f"{name}_render.png",
        )

    assembly = assembly_preview()
    assembly.save(str(EXPORT / "Brufik_N3_mobile_base_v0_1.step"))

    # Lightweight previews that can be reviewed without a CAD application.
    for name, part in {
        "cowl": parts["N3_upper_cowl_v0_1"],
        "adapter": parts["Brufik_to_N3_adapter_v0_1"],
    }.items():
        svg = exporters.getSVG(
            part.val(),
            {
                "width": 900,
                "height": 650,
                "marginLeft": 30,
                "marginTop": 30,
                "projectionDir": (1.0, -1.0, 0.75),
                "showAxes": False,
                "strokeWidth": 0.7,
                "hiddenColor": (150, 150, 150),
                "showHidden": True,
            },
        )
        svg_path = PREVIEW / f"{name}_isometric.svg"
        svg_path.write_text(svg, encoding="utf-8")
        if cairosvg is not None:
            cairosvg.svg2png(
                bytestring=svg.encode("utf-8"),
                write_to=str(PREVIEW / f"{name}_isometric.png"),
                output_width=1200,
                output_height=867,
            )

    manifest = {
        "revision": "0.1",
        "units": "mm",
        "n3_assumptions": asdict(N3),
        "brufik_assumptions": asdict(BRUFIK),
        "parts": measurements,
    }
    (EXPORT / "dimensions.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

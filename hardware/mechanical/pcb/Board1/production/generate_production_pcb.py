#!/usr/bin/env python3
"""Generate the routed Brufik Board1 PCB used for the JLCPCB prototype order.

The component locations come from the author's PickAndPlace export.  Footprints
under vendor/Board1.pretty are conversions of the exact LCSC/EasyEDA parts in
the author's BOM.  The board outline was sampled from the PCB solid contained
in ``Brufik step all v1.01.STP``.

This generator intentionally keeps the routing deterministic so the committed
KiCad board and Gerbers can be audited and regenerated.
"""

from __future__ import annotations

import copy
import heapq
import math
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "Board1.pretty"
OUTPUT = HERE / "Board1_production.kicad_pcb"

# Local tooling path used while reconstructing the project.  A normal
# ``pip install kiutils`` also works and is preferred on other machines.
LOCAL_KIUTILS = Path(r"C:\codes\小歪\.tools\kiutils")
if LOCAL_KIUTILS.exists():
    sys.path.insert(0, str(LOCAL_KIUTILS))

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from kiutils.items.gritems import GrLine
from kiutils.items.zones import FillSettings, Hatch, Zone, ZonePolygon


SHIFT_X = 50.0
SHIFT_Y = 50.0
GRID = 0.25
CLEARANCE = 0.22
EDGE_CLEARANCE = 0.35
VIA_SIZE = 0.8
VIA_DRILL = 0.4

NETS = {
    "": 0,
    "+5V": 1,
    "GND": 2,
    "+3V3": 3,
    "D0/I2S_DIN": 4,
    "D1/LCD_CS": 5,
    "D2/LCD_DC": 6,
    "D4/I2S_LRC": 7,
    "D5/I2S_BCLK": 8,
    "D6/SERVO_Y": 9,
    "D7/SERVO_X": 10,
    "D8/LCD_SCK": 11,
    "D10/LCD_MOSI": 12,
    "SPK+": 13,
    "SPK-": 14,
    "CC1": 15,
    "CC2": 16,
}
NET_NAMES = {number: name for name, number in NETS.items()}

# Ordered counter-clockwise.  The source STEP curves were sampled at eight
# intervals per edge.  The closing point is not repeated.
OUTLINE = [
    (-17.8166, 18.7755), (-17.8167, 16.6551), (-17.8168, 14.5348),
    (-17.8169, 12.4144), (-17.8170, 10.2941), (-17.8171, 8.1737),
    (-17.8171, 6.0534), (-17.8172, 3.9330), (-17.8173, 1.8127),
    (-17.8172, -0.3202), (-17.8171, -2.4531), (-17.8171, -4.5860),
    (-17.8170, -6.7190), (-17.8169, -8.8519), (-17.8168, -10.9848),
    (-17.8167, -13.1177), (-17.8166, -15.2506),
    (-17.7957, -15.6350), (-17.7192, -16.0245), (-17.5850, -16.4069),
    (-17.3947, -16.7697), (-17.1534, -17.1008), (-16.8697, -17.3903),
    (-16.5543, -17.6312), (-16.2188, -17.8199),
    (-15.8329, -18.0046), (-15.4414, -18.1785), (-15.0449, -18.3413),
    (-14.6437, -18.4929), (-14.2384, -18.6330), (-13.8294, -18.7616),
    (-13.4173, -18.8784), (-13.0026, -18.9833),
    (-12.1275, -19.1609), (-11.2470, -19.3152), (-10.3620, -19.4458),
    (-9.4735, -19.5525), (-8.5825, -19.6354), (-7.6899, -19.6942),
    (-6.7966, -19.7290), (-5.9038, -19.7398),
    (-5.7106, -19.7532), (-5.5204, -19.8085), (-5.3464, -19.9033),
    (-5.1999, -20.0300), (-5.0833, -20.1845), (-5.0001, -20.3643),
    (-4.9575, -20.5578), (-4.9569, -20.7514),
    (-4.9569, -20.8922), (-4.9569, -21.0329), (-4.9569, -21.1737),
    (-4.9569, -21.3145), (-4.9569, -21.4553), (-4.9569, -21.5961),
    (-4.9569, -21.7368), (-4.9569, -21.8776),
    (-3.7576, -21.8776), (-2.5583, -21.8776), (-1.3591, -21.8776),
    (-0.1598, -21.8776), (1.0395, -21.8776), (2.2387, -21.8776),
    (3.4380, -21.8776), (4.6373, -21.8776),
    (4.6373, -21.7368), (4.6373, -21.5961), (4.6373, -21.4553),
    (4.6373, -21.3145), (4.6373, -21.1737), (4.6373, -21.0329),
    (4.6373, -20.8922), (4.6373, -20.7514),
    (4.6379, -20.5578), (4.6805, -20.3643), (4.7637, -20.1845),
    (4.8803, -20.0300), (5.0268, -19.9034), (5.2007, -19.8086),
    (5.3909, -19.7533), (5.5840, -19.7398),
    (6.0334, -19.7283), (6.4827, -19.7131), (6.9319, -19.6941),
    (7.3809, -19.6713), (7.8297, -19.6448), (8.2783, -19.6145),
    (8.7266, -19.5805), (9.1745, -19.5427),
    (10.0409, -19.4695), (10.9058, -19.3549), (11.7662, -19.1989),
    (12.6191, -19.0019), (13.4617, -18.7645), (14.2911, -18.4876),
    (15.1045, -18.1722), (15.8994, -17.8198),
    (16.2417, -17.6259), (16.5632, -17.3776), (16.8513, -17.0787),
    (17.0946, -16.7366), (17.2843, -16.3621), (17.4149, -15.9681),
    (17.4853, -15.5680), (17.4977, -15.1747),
    (17.4976, -10.9310), (17.4976, -6.6872), (17.4975, -2.4434),
    (17.4974, 1.8004), (17.4973, 6.0442), (17.4972, 10.2879),
    (17.4971, 14.5317), (17.4970, 18.7755),
    (17.4762, 19.1598), (17.3996, 19.5493), (17.2654, 19.9317),
    (17.0751, 20.2945), (16.8339, 20.6256), (16.5503, 20.9151),
    (16.2348, 21.1560), (15.8993, 21.3447),
    (15.2081, 21.6578), (14.5019, 21.9413), (13.7825, 22.1940),
    (13.0520, 22.4151), (12.3125, 22.6040), (11.5661, 22.7600),
    (10.8151, 22.8829), (10.0616, 22.9726),
    (9.0508, 23.0683), (8.0383, 23.1486), (7.0244, 23.2136),
    (6.0096, 23.2632), (4.9942, 23.2974), (3.9784, 23.3162),
    (2.9628, 23.3196), (1.9475, 23.3075),
    (1.2577, 23.3067), (0.5678, 23.3059), (-0.1220, 23.3051),
    (-0.8119, 23.3043), (-1.5017, 23.3035), (-2.1915, 23.3027),
    (-2.8814, 23.3019), (-3.5712, 23.3011),
    (-4.3235, 23.3002), (-5.0758, 23.2909), (-5.8281, 23.2732),
    (-6.5801, 23.2471), (-7.3319, 23.2126), (-8.0831, 23.1698),
    (-8.8338, 23.1186), (-9.5837, 23.0590),
    (-10.4387, 22.9840), (-11.2921, 22.8685), (-12.1410, 22.7124),
    (-12.9825, 22.5161), (-13.8138, 22.2802), (-14.6320, 22.0055),
    (-15.4346, 21.6932), (-16.2190, 21.3447),
    (-16.5544, 21.1560), (-16.8699, 20.9151), (-17.1535, 20.6256),
    (-17.3947, 20.2945), (-17.5850, 19.9317), (-17.7192, 19.5493),
    (-17.7957, 19.1598),
]


@dataclass
class Placement:
    ref: str
    footprint: str
    value: str
    x: float
    y: float
    rotation: float
    layer: str
    pad_nets: dict[str, str]


PLACEMENTS = [
    Placement(
        "J1", "CONN-SMD_8P-P1.25_ZX-MX1.25-8PLT", "HEAD_8P",
        -0.127, -5.461, 0, "F.Cu",
        {
            "1": "+3V3", "2": "GND", "3": "D10/LCD_MOSI",
            "4": "D8/LCD_SCK", "5": "D1/LCD_CS", "6": "D2/LCD_DC",
            "7": "SPK+", "8": "SPK-", "9": "GND", "10": "GND",
        },
    ),
    Placement(
        "USB1", "TYPE-C-SMD_TYPE-C-6P-073", "TYPE-C 6P(073)",
        -0.150, -17.018, 0, "F.Cu",
        {
            "A12": "GND", "B12": "GND", "A9": "+5V", "B9": "+5V",
            "A5": "CC1", "B5": "CC2", "7": "GND",
        },
    ),
    Placement(
        "J4", "HDR-TH_3P-P2.54-V-M_PZ254V-11-03P", "SERVO_Y",
        -12.478, -15.644, 0, "F.Cu",
        {"1": "GND", "2": "+5V", "3": "D6/SERVO_Y"},
    ),
    Placement(
        "J3", "HDR-TH_3P-P2.54-H-M-W10.4", "SERVO_X",
        13.227, 19.180, 0, "F.Cu",
        {"1": "GND", "2": "+5V", "3": "D7/SERVO_X"},
    ),
    Placement(
        "H3", "HDR-TH_7P-P2.54-V-M", "XIAO_A",
        6.477, 12.573, 270, "F.Cu",
        {
            "1": "D0/I2S_DIN", "2": "D1/LCD_CS", "3": "D2/LCD_DC",
            "5": "D4/I2S_LRC", "6": "D5/I2S_BCLK", "7": "GND",
        },
    ),
    Placement(
        "H4", "HDR-TH_7P-P2.54-V-M", "XIAO_B",
        -8.763, 12.573, 270, "F.Cu",
        {
            "1": "+5V", "2": "+3V3", "3": "D6/SERVO_Y",
            "4": "D7/SERVO_X", "5": "D8/LCD_SCK",
            "7": "D10/LCD_MOSI",
        },
    ),
    Placement(
        "J2", "HDR-TH_7P-P2.54-H-M-W10.4", "AMP_7P",
        -12.700, 1.778, 270, "F.Cu",
        {
            "1": "+3V3", "2": "GND", "3": "D0/I2S_DIN",
            "4": "D5/I2S_BCLK", "5": "D4/I2S_LRC",
            "6": "SPK+", "7": "SPK-",
        },
    ),
    Placement(
        "R2", "R0805", "5.1k CC2",
        7.131, -13.081, 180, "B.Cu",
        {"1": "CC2", "2": "GND"},
    ),
    Placement(
        "R1", "R0805", "5.1k CC1",
        8.382, -16.383, 180, "B.Cu",
        {"1": "CC1", "2": "GND"},
    ),
]


@dataclass
class PadGeom:
    ref: str
    number: str
    x: float
    y: float
    rx: float
    ry: float
    layers: frozenset[int]
    net: int
    smd: bool


@dataclass
class RoutedSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: int
    net: int


@dataclass
class RoutedVia:
    x: float
    y: float
    size: float
    net: int


def uid() -> str:
    return str(uuid.uuid4())


def flip_layer(layer: str) -> str:
    if layer.startswith("F."):
        return "B." + layer[2:]
    if layer.startswith("B."):
        return "F." + layer[2:]
    return layer


def rotate_point(x: float, y: float, degrees: float) -> tuple[float, float]:
    # KiCad's board Y axis points down, so positive footprint angles transform
    # local coordinates clockwise in Cartesian terms.
    radians = math.radians(-degrees)
    c, s = math.cos(radians), math.sin(radians)
    return x * c - y * s, x * s + y * c


def absolute_pad_position(
    placement: Placement, local_x: float, local_y: float
) -> tuple[float, float]:
    # KiCad stores footprint-local pad coordinates unchanged on B.Cu; the
    # footprint rotation still applies in the usual way.  Mirroring local X
    # here would swap pads 1 and 2 on the two CC resistors.
    dx, dy = rotate_point(local_x, local_y, placement.rotation)
    return placement.x + dx, placement.y + dy


def load_footprints() -> tuple[list[Footprint], list[PadGeom]]:
    footprints: list[Footprint] = []
    pad_geometries: list[PadGeom] = []

    for placement in PLACEMENTS:
        source = VENDOR / f"{placement.footprint}.kicad_mod"
        if not source.exists():
            raise FileNotFoundError(source)
        fp = Footprint.from_file(str(source), encoding="utf-8")
        fp = copy.deepcopy(fp)
        fp.libId = f"Board1:{placement.footprint}"
        fp.position = Position(
            X=placement.x + SHIFT_X,
            Y=placement.y + SHIFT_Y,
            angle=placement.rotation,
        )
        fp.tstamp = uid()
        fp.models = []
        fp.layer = placement.layer

        if placement.layer == "B.Cu":
            for item in fp.graphicItems:
                if hasattr(item, "layer"):
                    item.layer = flip_layer(item.layer)
            for pad in fp.pads:
                pad.layers = [flip_layer(layer) for layer in pad.layers]

        for item in fp.graphicItems:
            if getattr(item, "type", None) == "reference":
                item.text = placement.ref
                item.tstamp = uid()
            elif getattr(item, "type", None) == "value":
                item.text = placement.value
                item.hide = True
                item.tstamp = uid()
            elif hasattr(item, "tstamp"):
                item.tstamp = uid()

        for pad in fp.pads:
            number = str(pad.number)
            net_name = placement.pad_nets.get(number, "")
            net_number = NETS[net_name]
            if net_number:
                pad.net = Net(number=net_number, name=net_name)
            else:
                pad.net = None
            pad.tstamp = uid()

            px, py = absolute_pad_position(
                placement, float(pad.position.X), float(pad.position.Y)
            )
            copper_layers: set[int] = set()
            if any(layer in ("*.Cu", "F&B.Cu") for layer in pad.layers):
                copper_layers.update((0, 1))
            if "F.Cu" in pad.layers:
                copper_layers.add(0)
            if "B.Cu" in pad.layers:
                copper_layers.add(1)
            pad_geometries.append(
                PadGeom(
                    ref=placement.ref,
                    number=number,
                    x=px,
                    y=py,
                    rx=float(pad.size.X) / 2,
                    ry=float(pad.size.Y) / 2,
                    layers=frozenset(copper_layers),
                    net=net_number,
                    smd=pad.type == "smd",
                )
            )

        footprints.append(fp)

    return footprints, pad_geometries


def point_in_polygon(x: float, y: float) -> bool:
    inside = False
    j = len(OUTLINE) - 1
    for i, (xi, yi) in enumerate(OUTLINE):
        xj, yj = OUTLINE[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_segment_distance(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def edge_distance(x: float, y: float) -> float:
    best = float("inf")
    for i, (x1, y1) in enumerate(OUTLINE):
        x2, y2 = OUTLINE[(i + 1) % len(OUTLINE)]
        best = min(best, point_segment_distance(x, y, x1, y1, x2, y2))
    return best


class Router:
    def __init__(self, pads: list[PadGeom]):
        self.pads = pads
        self.segments: list[RoutedSegment] = []
        self.vias: list[RoutedVia] = []
        self.min_x = -18.0
        self.min_y = -22.0
        self.max_x = 17.75
        self.max_y = 23.50
        self.nx = round((self.max_x - self.min_x) / GRID) + 1
        self.ny = round((self.max_y - self.min_y) / GRID) + 1
        self._blocked_cache: dict[
            tuple[int, int, int, int, float, bool], bool
        ] = {}
        self.inside_grid = {
            (ix, iy)
            for ix in range(self.nx)
            for iy in range(self.ny)
            if (
                point_in_polygon(*self.grid_to_xy(ix, iy))
                and edge_distance(*self.grid_to_xy(ix, iy)) >= EDGE_CLEARANCE
            )
        }

    def grid_to_xy(self, ix: int, iy: int) -> tuple[float, float]:
        return self.min_x + ix * GRID, self.min_y + iy * GRID

    def xy_to_grid(self, x: float, y: float) -> tuple[int, int]:
        return (
            round((x - self.min_x) / GRID),
            round((y - self.min_y) / GRID),
        )

    def blocked(
        self,
        ix: int,
        iy: int,
        layer: int,
        net: int,
        width: float,
        for_via: bool = False,
    ) -> bool:
        if ix < 0 or iy < 0 or ix >= self.nx or iy >= self.ny:
            return True
        key = (ix, iy, layer, net, width, for_via)
        cached = self._blocked_cache.get(key)
        if cached is not None:
            return cached
        x, y = self.grid_to_xy(ix, iy)
        if (ix, iy) not in self.inside_grid:
            return True
        result = self.blocked_xy(x, y, layer, net, width, for_via)
        self._blocked_cache[key] = result
        return result

    def blocked_xy(
        self,
        x: float,
        y: float,
        layer: int,
        net: int,
        width: float,
        for_via: bool = False,
    ) -> bool:
        item_radius = (VIA_SIZE if for_via else width) / 2
        for pad in self.pads:
            if layer not in pad.layers or pad.net == net:
                continue
            dx = max(abs(x - pad.x) - pad.rx, 0.0)
            dy = max(abs(y - pad.y) - pad.ry, 0.0)
            if math.hypot(dx, dy) < CLEARANCE + item_radius:
                return True

        for segment in self.segments:
            if segment.layer != layer or segment.net == net:
                continue
            needed = segment.width / 2 + item_radius + CLEARANCE
            if point_segment_distance(
                x, y, segment.x1, segment.y1, segment.x2, segment.y2
            ) < needed:
                return True

        for via in self.vias:
            if via.net == net:
                continue
            if math.hypot(x - via.x, y - via.y) < (
                via.size / 2 + item_radius + CLEARANCE
            ):
                return True
        return False

    def movement_blocked(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        layer: int,
        net: int,
        width: float,
    ) -> bool:
        """Check the complete proposed segment, not only its endpoints.

        With 2.54 mm through-hole headers, a diagonal grid edge can cross a
        neighbouring pad while both grid vertices remain clear.  Grid moves
        need only a midpoint check because their maximum length is 0.354 mm.
        Pad escape/entry stubs use denser sampling below.
        """
        distance = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, math.ceil(distance / (GRID / 2)))
        for step in range(steps + 1):
            t = step / steps
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            if self.blocked_xy(x, y, layer, net, width):
                return True
        return False

    def via_allowed(self, ix: int, iy: int, net: int) -> bool:
        x, y = self.grid_to_xy(ix, iy)
        for pad in self.pads:
            if not pad.smd:
                continue
            # Do not put an unfilled via in or immediately beside an SMD pad.
            dx = max(abs(x - pad.x) - pad.rx, 0.0)
            dy = max(abs(y - pad.y) - pad.ry, 0.0)
            if math.hypot(dx, dy) < 0.35:
                return False
        return not self.blocked(ix, iy, 0, net, VIA_SIZE, True) and not self.blocked(
            ix, iy, 1, net, VIA_SIZE, True
        )

    def route(
        self,
        start: PadGeom,
        target: PadGeom,
        net: int,
        width: float,
        preferred_layer: int,
    ) -> None:
        # Existing copper is immutable during this A* search, so grid obstacle
        # results can be cached.  Clear between routes after new copper lands.
        self._blocked_cache.clear()
        start_ix, start_iy = self.xy_to_grid(start.x, start.y)
        goal_ix, goal_iy = self.xy_to_grid(target.x, target.y)
        starts = [(start_ix, start_iy, layer) for layer in start.layers]
        goals = {(goal_ix, goal_iy, layer) for layer in target.layers}
        if not starts or not goals:
            raise RuntimeError(f"Pad without copper layer: {start} -> {target}")

        queue: list[tuple[float, float, tuple[int, int, int]]] = []
        cost: dict[tuple[int, int, int], float] = {}
        parent: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}

        def heuristic(state: tuple[int, int, int]) -> float:
            ix, iy, layer = state
            return min(
                math.hypot(ix - gx, iy - gy)
                + (8.0 if layer != goal_layer else 0.0)
                for gx, gy, goal_layer in goals
            )

        for state in starts:
            cost[state] = 0.0
            parent[state] = None
            heapq.heappush(queue, (heuristic(state), 0.0, state))

        found: tuple[int, int, int] | None = None
        directions = [
            (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
            (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)),
        ]

        while queue:
            _, current_cost, state = heapq.heappop(queue)
            if current_cost != cost.get(state):
                continue
            if state in goals:
                found = state
                break
            ix, iy, layer = state
            for dx, dy, move_cost in directions:
                nxt = (ix + dx, iy + dy, layer)
                if (
                    nxt[0] < 0
                    or nxt[1] < 0
                    or nxt[0] >= self.nx
                    or nxt[1] >= self.ny
                    or (nxt[0], nxt[1]) not in self.inside_grid
                ):
                    continue
                if self.blocked(nxt[0], nxt[1], layer, net, width):
                    continue
                x1, y1 = self.grid_to_xy(ix, iy)
                x2, y2 = self.grid_to_xy(nxt[0], nxt[1])
                # Endpoints were already checked on the routing grid.  The
                # midpoint catches a diagonal that clips a pad/track corner.
                if self.blocked_xy(
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    layer,
                    net,
                    width,
                ):
                    continue
                penalty = 0.04 if layer != preferred_layer else 0.0
                new_cost = current_cost + move_cost + penalty
                if new_cost < cost.get(nxt, float("inf")):
                    cost[nxt] = new_cost
                    parent[nxt] = state
                    heapq.heappush(
                        queue, (new_cost + heuristic(nxt), new_cost, nxt)
                    )
            other = (ix, iy, 1 - layer)
            if self.via_allowed(ix, iy, net):
                new_cost = current_cost + 9.0
                if new_cost < cost.get(other, float("inf")):
                    cost[other] = new_cost
                    parent[other] = state
                    heapq.heappush(
                        queue, (new_cost + heuristic(other), new_cost, other)
                    )

        if found is None:
            raise RuntimeError(
                f"Unable to route {NET_NAMES[net]}: "
                f"{start.ref}.{start.number} -> {target.ref}.{target.number}"
            )

        path: list[tuple[int, int, int]] = []
        cursor: tuple[int, int, int] | None = found
        while cursor is not None:
            path.append(cursor)
            cursor = parent[cursor]
        path.reverse()
        self._commit_path(path, start, target, net, width)

    def _commit_path(
        self,
        path: list[tuple[int, int, int]],
        start: PadGeom,
        target: PadGeom,
        net: int,
        width: float,
    ) -> None:
        points: list[tuple[float, float, int]] = []
        first_x, first_y = self.grid_to_xy(path[0][0], path[0][1])
        if self.movement_blocked(
            start.x, start.y, first_x, first_y, path[0][2], net, width
        ):
            raise RuntimeError(
                f"Blocked pad escape for {NET_NAMES[net]} at "
                f"{start.ref}.{start.number}"
            )
        points.append((start.x, start.y, path[0][2]))
        if math.hypot(start.x - first_x, start.y - first_y) > 1e-6:
            points.append((first_x, first_y, path[0][2]))
        for ix, iy, layer in path[1:]:
            x, y = self.grid_to_xy(ix, iy)
            points.append((x, y, layer))
        if math.hypot(points[-1][0] - target.x, points[-1][1] - target.y) > 1e-6:
            if self.movement_blocked(
                points[-1][0],
                points[-1][1],
                target.x,
                target.y,
                points[-1][2],
                net,
                width,
            ):
                raise RuntimeError(
                    f"Blocked pad entry for {NET_NAMES[net]} at "
                    f"{target.ref}.{target.number}"
                )
            points.append((target.x, target.y, points[-1][2]))

        # Remove collinear points without crossing a layer transition.
        simplified: list[tuple[float, float, int]] = []
        for point in points:
            simplified.append(point)
            while len(simplified) >= 3:
                a, b, c = simplified[-3:]
                if a[2] != b[2] or b[2] != c[2]:
                    break
                ab = (round(b[0] - a[0], 6), round(b[1] - a[1], 6))
                bc = (round(c[0] - b[0], 6), round(c[1] - b[1], 6))
                if abs(ab[0] * bc[1] - ab[1] * bc[0]) > 1e-6:
                    break
                simplified.pop(-2)

        for a, b in zip(simplified, simplified[1:]):
            if a[2] != b[2]:
                self.vias.append(RoutedVia(a[0], a[1], VIA_SIZE, net))
            elif math.hypot(a[0] - b[0], a[1] - b[1]) > 1e-6:
                self.segments.append(
                    RoutedSegment(a[0], a[1], b[0], b[1], width, a[2], net)
                )


def pad_index(pads: list[PadGeom]) -> dict[tuple[str, str], list[PadGeom]]:
    result: dict[tuple[str, str], list[PadGeom]] = {}
    for pad in pads:
        result.setdefault((pad.ref, pad.number), []).append(pad)
    return result


def add_routes(router: Router, pads: list[PadGeom]) -> None:
    by_key = pad_index(pads)

    def P(ref: str, number: str, occurrence: int = 0) -> PadGeom:
        return by_key[(ref, number)][occurrence]

    # High-current 5 V distribution.  The USB-C contact pitch requires a short
    # 0.5 mm neck from each VBUS pad.  Two vias then feed a 1.0/1.2 mm B.Cu
    # trunk; this avoids via-in-pad solder wicking and keeps the servo path wide.
    vbus_a_top = PadGeom(
        "TP5A", "1", 1.37, -14.75, 0.4, 0.4,
        frozenset({0}), NETS["+5V"], False,
    )
    vbus_b_top = PadGeom(
        "TP5B", "1", -1.67, -14.75, 0.4, 0.4,
        frozenset({0}), NETS["+5V"], False,
    )
    vbus_a_bottom = copy.copy(vbus_a_top)
    vbus_a_bottom.layers = frozenset({1})
    vbus_b_bottom = copy.copy(vbus_b_top)
    vbus_b_bottom.layers = frozenset({1})
    router.route(P("USB1", "A9"), vbus_a_top, NETS["+5V"], 0.50, 0)
    router.route(P("USB1", "B9"), vbus_b_top, NETS["+5V"], 0.50, 0)
    router.vias.extend(
        [
            RoutedVia(vbus_a_top.x, vbus_a_top.y, VIA_SIZE, NETS["+5V"]),
            RoutedVia(vbus_b_top.x, vbus_b_top.y, VIA_SIZE, NETS["+5V"]),
        ]
    )
    router.route(
        vbus_a_bottom, vbus_b_bottom, NETS["+5V"], 1.00, 1
    )
    router.route(
        vbus_b_bottom, P("H4", "1"), NETS["+5V"], 1.20, 1
    )
    router.route(P("H4", "1"), P("J3", "2"), NETS["+5V"], 1.20, 1)
    router.route(
        vbus_b_bottom, P("J4", "2"), NETS["+5V"], 1.20, 1
    )

    # 3.3 V rail.
    for start, end in [
        (P("H4", "2"), P("J1", "1")),
        (P("H4", "2"), P("J2", "1")),
    ]:
        router.route(start, end, NETS["+3V3"], 0.60, 1)

    # Audio output pair.
    for net_name, start, end, preferred in [
        ("SPK+", P("J1", "7"), P("J2", "6"), 0),
        ("SPK-", P("J1", "8"), P("J2", "7"), 1),
    ]:
        router.route(start, end, NETS[net_name], 0.45, preferred)

    # Digital signals.
    signal_routes = [
        ("D10/LCD_MOSI", P("H4", "7"), P("J1", "3"), 0),
        ("D8/LCD_SCK", P("H4", "5"), P("J1", "4"), 1),
        ("D1/LCD_CS", P("H3", "2"), P("J1", "5"), 1),
        ("D2/LCD_DC", P("H3", "3"), P("J1", "6"), 0),
        ("D0/I2S_DIN", P("H3", "1"), P("J2", "3"), 0),
        ("D4/I2S_LRC", P("H3", "5"), P("J2", "5"), 1),
        ("D5/I2S_BCLK", P("H3", "6"), P("J2", "4"), 0),
        ("D6/SERVO_Y", P("H4", "3"), P("J4", "3"), 1),
        ("D7/SERVO_X", P("H4", "4"), P("J3", "3"), 0),
    ]
    for net_name, start, end, preferred in signal_routes:
        router.route(start, end, NETS[net_name], 0.25, preferred)

    # USB-C configuration resistors are on B.Cu.
    router.route(P("USB1", "A5"), P("R1", "1"), NETS["CC1"], 0.25, 1)
    router.route(P("USB1", "B5"), P("R2", "1"), NETS["CC2"], 0.25, 1)


def add_ground_stitching(router: Router) -> None:
    candidates = [
        (-14.5, -11.0), (-9.0, -10.5), (0.0, -10.5), (11.5, -9.5),
        (-15.0, 12.0), (-4.0, 1.0), (4.0, 1.0), (14.0, 10.0),
        (-13.0, 21.0), (-4.0, 21.5), (3.0, 21.5), (9.5, 21.0),
    ]
    for x, y in candidates:
        ix, iy = router.xy_to_grid(x, y)
        if router.via_allowed(ix, iy, NETS["GND"]):
            gx, gy = router.grid_to_xy(ix, iy)
            router.vias.append(RoutedVia(gx, gy, VIA_SIZE, NETS["GND"]))


def make_board() -> Board:
    board = Board.create_new()
    board.version = "20231120"
    board.generator = "brufik_board1_production_generator"
    board.general.thickness = 1.6
    board.nets = [
        Net(number=number, name=name)
        for name, number in sorted(NETS.items(), key=lambda item: item[1])
    ]

    footprints, pad_geometries = load_footprints()
    board.footprints = footprints

    # Exact STEP-derived outline.
    for i, start in enumerate(OUTLINE):
        end = OUTLINE[(i + 1) % len(OUTLINE)]
        board.graphicItems.append(
            GrLine(
                start=Position(X=start[0] + SHIFT_X, Y=start[1] + SHIFT_Y),
                end=Position(X=end[0] + SHIFT_X, Y=end[1] + SHIFT_Y),
                layer="Edge.Cuts",
                width=0.05,
                tstamp=uid(),
            )
        )

    router = Router(pad_geometries)
    add_routes(router, pad_geometries)
    add_ground_stitching(router)

    for segment in router.segments:
        board.traceItems.append(
            Segment(
                start=Position(
                    X=round(segment.x1 + SHIFT_X, 4),
                    Y=round(segment.y1 + SHIFT_Y, 4),
                ),
                end=Position(
                    X=round(segment.x2 + SHIFT_X, 4),
                    Y=round(segment.y2 + SHIFT_Y, 4),
                ),
                width=segment.width,
                layer="F.Cu" if segment.layer == 0 else "B.Cu",
                net=segment.net,
                tstamp=uid(),
            )
        )
    for via in router.vias:
        board.traceItems.append(
            Via(
                position=Position(
                    X=round(via.x + SHIFT_X, 4),
                    Y=round(via.y + SHIFT_Y, 4),
                ),
                size=via.size,
                drill=VIA_DRILL,
                layers=["F.Cu", "B.Cu"],
                net=via.net,
                tstamp=uid(),
            )
        )

    # Ground planes on both layers provide short return paths for I2S/SPI and
    # the two servo rails.  KiCad fills them before Gerber export.
    zone_outline = [
        Position(X=x + SHIFT_X, Y=y + SHIFT_Y) for x, y in OUTLINE
    ]
    for layer in ("F.Cu", "B.Cu"):
        board.zones.append(
            Zone(
                net=NETS["GND"],
                netName="GND",
                layers=[layer],
                tstamp=uid(),
                hatch=Hatch(style="edge", pitch=0.5),
                clearance=CLEARANCE,
                minThickness=0.20,
                fillSettings=FillSettings(
                    yes=True,
                    thermalGap=0.25,
                    thermalBridgeWidth=0.30,
                    islandRemovalMode=0,
                ),
                polygons=[ZonePolygon(coordinates=zone_outline)],
            )
        )

    return board


def main() -> None:
    board = make_board()
    board.to_file(str(OUTPUT), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print(
        f"Footprints={len(board.footprints)} "
        f"tracks={sum(isinstance(item, Segment) for item in board.traceItems)} "
        f"vias={sum(isinstance(item, Via) for item in board.traceItems)} "
        f"zones={len(board.zones)}"
    )


if __name__ == "__main__":
    main()

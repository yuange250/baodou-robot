#!/usr/bin/env python3
"""Generate the Brufik N3 mobile-base controller PCB.

The script uses KiCad's bundled ``pcbnew`` Python module so the saved board is
native KiCad data rather than a hand-written approximation.  It also emits the
BOM, pinout, connection table and a readable block schematic.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pcbnew


ROOT = Path(__file__).resolve().parent
PROJECT = "BrufikMobileBase"
BOARD_PATH = ROOT / f"{PROJECT}.kicad_pcb"
BUILD = ROOT / "production"


def mm(value: float) -> int:
    return pcbnew.FromMM(value)


def point(x: float, y: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(mm(x), mm(y))


@dataclass
class Component:
    ref: str
    value: str
    footprint: str
    description: str
    lcsc: str = ""
    assembly: str = "SMT"


COMPONENTS: list[Component] = [
    Component("U1", "TPS54302DDCR", "SOT-23-6", "5V/3A synchronous buck", "C129704"),
    Component("U2", "DRV8833PWPR", "HTSSOP-16-EP", "Dual H-bridge for M1/M2", ""),
    Component("U3", "DRV8833PWPR", "HTSSOP-16-EP", "Dual H-bridge for M3", ""),
    Component("U4", "Seeed XIAO ESP32-C3", "XIAO-2x7-P2.54", "Base motion MCU", "", "HAND"),
    Component("F1", "5A resettable fuse", "PTC-2920", "Battery branch protection", "", "SMT"),
    Component("D1", "SMBJ10A", "SMB", "Battery transient suppressor", "C83357"),
    Component("L1", "10uH 4A shielded", "IND-7x7", "5V buck output inductor", "", "SMT"),
    Component("C1", "22uF 16V X7R", "C1210", "Buck input capacitor"),
    Component("C2", "100nF 25V X7R", "C0805", "Buck HF input bypass"),
    Component("C3", "22uF 10V X7R", "C1210", "5V output capacitor"),
    Component("C4", "22uF 10V X7R", "C1210", "5V output capacitor"),
    Component("C5", "100nF 16V X7R", "C0805", "TPS54302 bootstrap capacitor"),
    Component("C6", "75pF C0G", "C0805", "5V feedback feed-forward capacitor"),
    Component("C7", "470uF 16V low-ESR", "CP-RADIAL-D8", "Motor/battery local bulk", "", "HAND"),
    Component("C8", "1000uF 10V low-ESR", "CP-RADIAL-D10", "Upper-body 5V surge bulk", "", "HAND"),
    Component("C9", "10uF 16V X7R", "C1210", "U2 VM local bypass"),
    Component("C10", "2.2uF 10V X7R", "C0805", "U2 VINT bypass"),
    Component("C11", "10nF 16V X7R", "C0805", "U2 VCP capacitor"),
    Component("C12", "10uF 16V X7R", "C1210", "U3 VM local bypass"),
    Component("C13", "2.2uF 10V X7R", "C0805", "U3 VINT bypass"),
    Component("C14", "10nF 16V X7R", "C0805", "U3 VCP capacitor"),
    Component("C15", "100nF 25V X7R", "C0805", "M1 EMI capacitor"),
    Component("C16", "100nF 25V X7R", "C0805", "M2 EMI capacitor"),
    Component("C17", "100nF 25V X7R", "C0805", "M3 EMI capacitor"),
    Component("R1", "100k 1%", "R0805", "5V feedback upper resistor"),
    Component("R2", "13.3k 1%", "R0805", "5V feedback lower resistor"),
    Component("R3", "100k 1%", "R0805", "Buck enable upper resistor"),
    Component("R4", "47k 1%", "R0805", "Buck enable lower resistor"),
    Component("R5", "10k", "R0805", "DRV nSLEEP pull-up"),
    Component("R7", "0.20R 1W 1%", "R2512", "M1 current limit, about 1A"),
    Component("R8", "0.20R 1W 1%", "R2512", "M2 current limit, about 1A"),
    Component("R9", "0.20R 1W 1%", "R2512", "M3 current limit, about 1A"),
    Component("J1", "BATTERY 2S", "TERM-2P-P5.08", "Protected 2S battery input", "", "HAND"),
    Component("J2", "POWER SWITCH", "JST-XH-2P", "External latching switch or jumper", "", "HAND"),
    Component("J3", "MOTOR 1", "JST-XH-2P", "N20 motor 1", "", "HAND"),
    Component("J4", "MOTOR 2", "JST-XH-2P", "N20 motor 2", "", "HAND"),
    Component("J5", "MOTOR 3", "JST-XH-2P", "N20 motor 3", "", "HAND"),
    Component("J6", "UPPER LINK", "JST-XH-4P", "5V/GND/TX/RX to current robot", "", "HAND"),
    Component("J7", "TOUCH", "JST-XH-3P", "3.3V/GND/touch signal", "", "HAND"),
]


NET_NAMES = [
    "GND",
    "BAT_RAW",
    "BAT_FUSED",
    "VBAT",
    "+5V",
    "+3V3",
    "BUCK_SW",
    "BUCK_FB",
    "BUCK_EN",
    "BUCK_BOOT",
    "M1_A",
    "M1_B",
    "M2_A",
    "M2_B",
    "M3_A",
    "M3_B",
    "M1_IN1",
    "M1_IN2",
    "M2_IN1",
    "M2_IN2",
    "M3_IN1",
    "M3_IN2",
    "UART_TX",
    "UART_RX",
    "TOUCH",
    "SLEEP",
    "U2_VINT",
    "U2_VCP",
    "U2_SENSE_A",
    "U2_SENSE_B",
    "U3_VINT",
    "U3_VCP",
    "U3_SENSE_A",
]


class PCB:
    def __init__(self) -> None:
        self.board = pcbnew.BOARD()
        self.board.SetCopperLayerCount(4)
        self.board.SetGenerator("brufik_mobile_base_generator")
        settings = self.board.GetDesignSettings()
        settings.m_SolderMaskExpansion = mm(0.03)
        settings.m_SolderMaskMinWidth = mm(0.10)
        settings.m_MinClearance = mm(0.18)
        settings.m_TrackMinWidth = mm(0.18)
        settings.m_HoleClearance = mm(0.20)
        title = self.board.GetTitleBlock()
        title.SetTitle("Brufik N3 Mobile Base Controller")
        title.SetDate("2026-08-06")
        title.SetRevision("0.1")
        title.SetCompany("OpenDeskBot / Brufik community build")

        self.nets: dict[str, pcbnew.NETINFO_ITEM] = {}
        for name in NET_NAMES:
            net = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(net)
            self.nets[name] = net

        self.footprints: dict[str, pcbnew.FOOTPRINT] = {}
        self.pads: dict[tuple[str, str], pcbnew.PAD] = {}

    def net(self, name: str) -> pcbnew.NETINFO_ITEM:
        return self.nets[name]

    def add_pad(
        self,
        fp: pcbnew.FOOTPRINT,
        ref: str,
        number: str,
        x: float,
        y: float,
        sx: float,
        sy: float,
        net: str | None,
        *,
        pth: bool = False,
        drill: float = 1.0,
        shape: int = pcbnew.PAD_SHAPE_RECT,
    ) -> pcbnew.PAD:
        pad = pcbnew.PAD(fp)
        pad.SetNumber(number)
        pad.SetPosition(point(x, y))
        pad.SetSize(point(sx, sy))
        pad.SetShape(shape)
        if pth:
            pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
            pad.SetDrillSize(point(drill, drill))
            pad.SetLayerSet(pad.PTHMask())
        else:
            pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            pad.SetLayerSet(pad.SMDMask())
        if net:
            pad.SetNet(self.net(net))
        fp.Add(pad)
        self.pads[(ref, number)] = pad
        return pad

    def add_fp_outline(
        self,
        fp: pcbnew.FOOTPRINT,
        x: float,
        y: float,
        sx: float,
        sy: float,
    ) -> None:
        corners = [
            (x - sx / 2, y - sy / 2),
            (x + sx / 2, y - sy / 2),
            (x + sx / 2, y + sy / 2),
            (x - sx / 2, y + sy / 2),
        ]
        for index, start in enumerate(corners):
            end = corners[(index + 1) % len(corners)]
            shape = pcbnew.PCB_SHAPE(fp)
            shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
            shape.SetStart(point(*start))
            shape.SetEnd(point(*end))
            shape.SetLayer(pcbnew.F_SilkS)
            shape.SetWidth(mm(0.15))
            fp.Add(shape)

    def make_fp(
        self,
        ref: str,
        value: str,
        x: float,
        y: float,
        body_x: float,
        body_y: float,
    ) -> pcbnew.FOOTPRINT:
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPosition(point(x, y))
        fp.SetLayer(pcbnew.F_Cu)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        self.board.Add(fp)
        self.footprints[ref] = fp
        return fp

    def two_pad(
        self,
        ref: str,
        value: str,
        x: float,
        y: float,
        net1: str,
        net2: str,
        *,
        pad_dx: float = 1.5,
        pad_size: tuple[float, float] = (1.6, 1.4),
        body: tuple[float, float] = (3.6, 2.0),
        vertical: bool = False,
    ) -> None:
        fp = self.make_fp(ref, value, x, y, *body)
        if vertical:
            self.add_pad(fp, ref, "1", x, y - pad_dx, *pad_size, net1)
            self.add_pad(fp, ref, "2", x, y + pad_dx, *pad_size, net2)
        else:
            self.add_pad(fp, ref, "1", x - pad_dx, y, *pad_size, net1)
            self.add_pad(fp, ref, "2", x + pad_dx, y, *pad_size, net2)

    def connector(
        self,
        ref: str,
        value: str,
        x: float,
        y: float,
        nets: Sequence[str],
        *,
        vertical: bool = False,
        pitch: float = 2.54,
        terminal: bool = False,
    ) -> None:
        count = len(nets)
        length = (count - 1) * pitch + 4.0
        body_x, body_y = (5.5, length) if vertical else (length, 5.5)
        fp = self.make_fp(ref, value, x, y, body_x, body_y)
        for index, net in enumerate(nets):
            offset = (index - (count - 1) / 2) * pitch
            px, py = (x, y + offset) if vertical else (x + offset, y)
            self.add_pad(
                fp,
                ref,
                str(index + 1),
                px,
                py,
                2.2 if terminal else 1.8,
                2.2 if terminal else 1.8,
                net,
                pth=True,
                drill=1.2 if terminal else 1.0,
                shape=pcbnew.PAD_SHAPE_RECT if index == 0 else pcbnew.PAD_SHAPE_CIRCLE,
            )

    def mounting_hole(self, ref: str, x: float, y: float) -> None:
        fp = self.make_fp(ref, "M3", x, y, 7.0, 7.0)
        pad = self.add_pad(
            fp,
            ref,
            "",
            x,
            y,
            3.6,
            3.6,
            None,
            pth=True,
            drill=3.6,
            shape=pcbnew.PAD_SHAPE_CIRCLE,
        )
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetLayerSet(pad.UnplatedHoleMask())
        pad.SetLocalClearance(mm(0.45))

    def driver(self, ref: str, x: float, y: float, nets: dict[int, str | None]) -> None:
        fp = self.make_fp(ref, "DRV8833PWPR", x, y, 7.2, 5.4)
        pitch = 0.65
        for pin in range(1, 9):
            py = y - 2.275 + (pin - 1) * pitch
            pad = self.add_pad(fp, ref, str(pin), x - 3.0, py, 1.6, 0.40, nets.get(pin))
            pad.SetLocalSolderMaskMargin(mm(-0.04))
        for pin in range(9, 17):
            py = y + 2.275 - (pin - 9) * pitch
            pad = self.add_pad(fp, ref, str(pin), x + 3.0, py, 1.6, 0.40, nets.get(pin))
            pad.SetLocalSolderMaskMargin(mm(-0.04))
        self.add_pad(
            fp,
            ref,
            "17",
            x,
            y,
            3.2,
            3.1,
            "GND",
            shape=pcbnew.PAD_SHAPE_RECT,
        )

    def buck(self, ref: str, x: float, y: float) -> None:
        nets = {
            1: "GND",
            2: "BUCK_SW",
            3: "VBAT",
            4: "BUCK_FB",
            5: "BUCK_EN",
            6: "BUCK_BOOT",
        }
        fp = self.make_fp(ref, "TPS54302DDCR", x, y, 5.4, 4.0)
        ys_left = [y + 1.9, y, y - 1.9]
        ys_right = [y - 1.9, y, y + 1.9]
        for pin, py in zip((1, 2, 3), ys_left):
            self.add_pad(fp, ref, str(pin), x - 1.9, py, 1.4, 0.8, nets[pin])
        for pin, py in zip((4, 5, 6), ys_right):
            self.add_pad(fp, ref, str(pin), x + 1.9, py, 1.4, 0.8, nets[pin])

    def xiao(self, ref: str, x: float, y: float) -> None:
        fp = self.make_fp(ref, "Seeed XIAO ESP32-C3", x, y, 18.2, 21.5)
        left_nets = [
            "M1_IN2",
            "M1_IN1",
            "M2_IN1",
            "M2_IN2",
            "M3_IN1",
            "M3_IN2",
            "UART_TX",
        ]
        right_nets = ["+5V", "GND", "+3V3", "TOUCH", None, None, "UART_RX"]
        y0 = y - 7.62
        for index, net in enumerate(left_nets):
            self.add_pad(
                fp,
                ref,
                f"L{index + 1}",
                x - 7.62,
                y0 + index * 2.54,
                1.8,
                1.8,
                net,
                pth=True,
                drill=1.0,
                shape=pcbnew.PAD_SHAPE_RECT if index == 0 else pcbnew.PAD_SHAPE_CIRCLE,
            )
        for index, net in enumerate(right_nets):
            self.add_pad(
                fp,
                ref,
                f"R{index + 1}",
                x + 7.62,
                y0 + index * 2.54,
                1.8,
                1.8,
                net,
                pth=True,
                drill=1.0,
                shape=pcbnew.PAD_SHAPE_CIRCLE,
            )

    def edge_polygon(self, points: Sequence[tuple[float, float]]) -> None:
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            shape = pcbnew.PCB_SHAPE(self.board)
            shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
            shape.SetStart(point(*start))
            shape.SetEnd(point(*end))
            shape.SetLayer(pcbnew.Edge_Cuts)
            shape.SetWidth(mm(0.1))
            self.board.Add(shape)

    def text(
        self,
        value: str,
        x: float,
        y: float,
        size: float = 1.0,
        layer: int = pcbnew.F_SilkS,
    ) -> None:
        item = pcbnew.PCB_TEXT(self.board)
        item.SetText(value)
        item.SetPosition(point(x, y))
        item.SetLayer(layer)
        item.SetTextSize(point(size, size))
        item.SetTextThickness(mm(max(0.15, size * 0.13)))
        self.board.Add(item)

    def pad_pos(self, ref: str, number: str) -> tuple[float, float]:
        p = self.pads[(ref, number)].GetPosition()
        return pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)

    def segment(
        self,
        net: str,
        start: tuple[float, float],
        end: tuple[float, float],
        width: float,
        layer: int,
    ) -> None:
        if start == end:
            return
        track = pcbnew.PCB_TRACK(self.board)
        track.SetNet(self.net(net))
        track.SetLayer(layer)
        track.SetWidth(mm(width))
        track.SetStart(point(*start))
        track.SetEnd(point(*end))
        self.board.Add(track)

    def route(
        self,
        net: str,
        coords: Sequence[tuple[float, float]],
        *,
        width: float = 0.28,
        layer: int = pcbnew.F_Cu,
    ) -> None:
        for start, end in zip(coords, coords[1:]):
            self.segment(net, start, end, width, layer)

    def route_pads(
        self,
        net: str,
        nodes: Sequence[tuple[str, str]],
        *,
        mids: Sequence[tuple[float, float]] = (),
        width: float = 0.28,
        layer: int = pcbnew.F_Cu,
    ) -> None:
        coords = [self.pad_pos(*nodes[0]), *mids, self.pad_pos(*nodes[1])]
        self.route(net, coords, width=width, layer=layer)

    def via(self, net: str, x: float, y: float, *, size: float = 0.8, drill: float = 0.4) -> None:
        via = pcbnew.PCB_VIA(self.board)
        via.SetNet(self.net(net))
        via.SetPosition(point(x, y))
        via.SetWidth(mm(size))
        via.SetDrill(mm(drill))
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        self.board.Add(via)

    def pad_to_via(
        self,
        net: str,
        ref: str,
        number: str,
        via_xy: tuple[float, float],
        *,
        width: float = 0.5,
    ) -> None:
        self.route(net, [self.pad_pos(ref, number), via_xy], width=width)
        self.via(net, *via_xy)

    def zone(
        self,
        net: str,
        layer: int,
        polygon: Sequence[tuple[float, float]],
        *,
        clearance: float = 0.25,
    ) -> None:
        zone = pcbnew.ZONE(self.board)
        zone.SetLayer(layer)
        zone.SetNet(self.net(net))
        zone.SetLocalClearance(mm(clearance))
        zone.SetMinThickness(mm(0.20))
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in polygon:
            outline.Append(point(x, y))
        self.board.Add(zone)


def populate(p: PCB) -> None:
    # The mounting pattern stays at the N3 standard 58 x 49 mm, while the PCB
    # itself is deliberately wider than the hole rectangle.  The extra routing
    # room keeps motor-current copper away from the 0.65 mm-pitch driver pins.
    p.edge_polygon([(4, 0), (88, 0), (92, 4), (92, 70), (88, 74), (4, 74), (0, 70), (0, 4)])
    for ref, x, y in [
        ("H1", 17, 12.5), ("H2", 75, 12.5),
        ("H3", 17, 61.5), ("H4", 75, 61.5),
    ]:
        p.mounting_hole(ref, x, y)

    p.connector("J1", "BATTERY 2S", 46, 69, ["BAT_RAW", "GND"], terminal=True)
    p.connector("J2", "POWER SWITCH", 34, 69, ["BAT_FUSED", "VBAT"])
    p.connector("J3", "MOTOR 1", 26, 5, ["M1_B", "M1_A"])
    p.connector("J4", "MOTOR 2", 5, 37, ["M2_B", "M2_A"], vertical=True)
    p.connector("J5", "MOTOR 3", 20, 69, ["M3_B", "M3_A"])
    p.connector("J6", "UPPER LINK", 87, 53, ["+5V", "GND", "UART_TX", "UART_RX"], vertical=True)
    p.connector("J7", "TOUCH", 87, 35, ["+3V3", "GND", "TOUCH"], vertical=True)

    # Battery protection/input path.
    p.two_pad("F1", "5A PTC", 41, 65, "BAT_FUSED", "BAT_RAW", pad_dx=2.7, pad_size=(2.5, 3.0), body=(7.5, 5.5))
    p.two_pad("D1", "SMBJ10A", 51, 59, "VBAT", "GND", pad_dx=2.5, pad_size=(2.8, 2.4), body=(6.5, 4.0))
    p.two_pad("C7", "470uF", 57, 64, "VBAT", "GND", pad_dx=2.5, pad_size=(2.4, 2.4), body=(8.0, 8.0))
    p.pads[("C7", "1")].SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    p.pads[("C7", "1")].SetDrillSize(point(1.0, 1.0))
    p.pads[("C7", "1")].SetLayerSet(p.pads[("C7", "1")].PTHMask())
    p.pads[("C7", "2")].SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    p.pads[("C7", "2")].SetDrillSize(point(1.0, 1.0))
    p.pads[("C7", "2")].SetLayerSet(p.pads[("C7", "2")].PTHMask())

    # Compact 5 V buck section in the upper-right quadrant.
    p.buck("U1", 55, 10)
    p.two_pad("L1", "10uH", 47, 10, "+5V", "BUCK_SW", pad_dx=2.4, pad_size=(2.6, 4.5), body=(7.0, 7.0))
    p.two_pad("C1", "22uF", 52, 5, "GND", "VBAT", pad_dx=1.5, pad_size=(1.8, 2.6), body=(4.0, 3.2), vertical=True)
    p.two_pad("C2", "100nF", 48, 5, "GND", "VBAT", vertical=True)
    p.two_pad("C3", "22uF", 39, 8, "+5V", "GND", pad_dx=1.5, pad_size=(1.8, 2.6), body=(4.0, 3.2), vertical=True)
    p.two_pad("C4", "22uF", 39, 14.5, "+5V", "GND", pad_dx=1.5, pad_size=(1.8, 2.6), body=(4.0, 3.2), vertical=True)
    p.two_pad("C5", "100nF", 55, 15, "BUCK_BOOT", "BUCK_SW", vertical=True)
    p.two_pad("R1", "100k", 63, 6, "+5V", "BUCK_FB", vertical=True)
    p.two_pad("R2", "13.3k", 63, 11, "BUCK_FB", "GND", vertical=True)
    p.two_pad("C6", "75pF", 67, 8, "+5V", "BUCK_FB", vertical=True)
    p.two_pad("R3", "100k", 59, 18, "VBAT", "BUCK_EN", vertical=True)
    p.two_pad("R4", "47k", 64, 18, "BUCK_EN", "GND", vertical=True)
    p.two_pad("C8", "1000uF", 83, 65, "+5V", "GND", pad_dx=3.0, pad_size=(2.6, 2.6), body=(10.0, 10.0))
    for number in ("1", "2"):
        pad = p.pads[("C8", number)]
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetDrillSize(point(1.0, 1.0))
        pad.SetLayerSet(pad.PTHMask())

    u2 = {
        1: "U2_SENSE_A", 2: "M1_B", 3: "M2_B", 4: "U2_SENSE_B",
        5: "M2_A", 6: None, 7: "M2_IN1", 8: "M2_IN2",
        9: "U2_VCP", 10: "VBAT", 11: "GND", 12: "U2_VINT",
        13: "M1_IN2", 14: "M1_IN1", 15: "SLEEP", 16: "M1_A",
    }
    u3 = {
        1: "U3_SENSE_A", 2: "M3_B", 3: None, 4: "GND",
        5: None, 6: None, 7: None, 8: None,
        9: "U3_VCP", 10: "VBAT", 11: "GND", 12: "U3_VINT",
        13: "M3_IN2", 14: "M3_IN1", 15: "SLEEP", 16: "M3_A",
    }
    p.driver("U2", 26, 27, u2)
    p.driver("U3", 26, 49, u3)
    p.xiao("U4", 51, 41)

    p.two_pad("C9", "10uF", 9, 15, "VBAT", "GND", pad_size=(1.8, 2.6), body=(4.0, 3.2))
    p.two_pad("C10", "2.2uF", 34, 31.5, "U2_VINT", "GND")
    p.two_pad("C11", "10nF", 34, 35, "U2_VCP", "VBAT")
    p.two_pad("C12", "10uF", 18, 42, "VBAT", "GND", pad_size=(1.8, 2.6), body=(4.0, 3.2))
    p.two_pad("C13", "2.2uF", 35, 49.3, "U3_VINT", "GND")
    p.two_pad("C14", "10nF", 35, 52.5, "U3_VCP", "VBAT")
    p.two_pad("R7", "0.20R", 8, 20, "GND", "U2_SENSE_A", pad_dx=2.2, pad_size=(2.5, 3.2), body=(6.3, 3.5))
    p.two_pad("R8", "0.20R", 8, 25, "GND", "U2_SENSE_B", pad_dx=2.2, pad_size=(2.5, 3.2), body=(6.3, 3.5))
    p.two_pad("R9", "0.20R", 8, 50, "GND", "U3_SENSE_A", pad_dx=2.2, pad_size=(2.5, 3.2), body=(6.3, 3.5))
    p.two_pad("R5", "10k", 44, 25, "+3V3", "SLEEP")
    p.two_pad("C15", "100nF", 26, 9, "M1_B", "M1_A")
    p.two_pad("C16", "100nF", 9, 37, "M2_B", "M2_A", vertical=True)
    p.two_pad("C17", "100nF", 22, 64, "M3_B", "M3_A")

    p.text("REV 0.1 / 2S INPUT", 64, 72.0, 0.8)
    p.text("M2", 8.0, 37, 0.9)
    p.text("M3", 20, 66, 0.9)
    p.text("UPPER", 83, 59, 0.8)
    p.text("TOUCH", 83, 41, 0.8)


def route_board(p: PCB) -> None:
    # Battery input and mandatory external switch/jumper.
    p.route("BAT_RAW", [p.pad_pos("J1", "1"), (44.7, 65), p.pad_pos("F1", "2")], width=1.8)
    p.route("BAT_FUSED", [p.pad_pos("F1", "1"), (35.5, 65), p.pad_pos("J2", "1")], width=1.8)

    # Inner planes: In1 = GND, In2 = switched battery.  The larger local
    # clearance also gives the four unplated mounting holes a robust annulus.
    plane = [(1, 1), (91, 1), (91, 73), (1, 73)]
    p.zone("GND", pcbnew.In1_Cu, plane, clearance=0.38)
    p.zone("VBAT", pcbnew.In2_Cu, plane, clearance=0.38)

    # VBAT plane access. Fine-pitch VM pins neck down before their vias.
    for ref, number in [
        ("U1", "3"), ("C1", "2"), ("C2", "2"), ("C9", "1"),
        ("C12", "1"), ("C14", "2"), ("D1", "1"),
        ("R3", "1"),
    ]:
        x, y = p.pad_pos(ref, number)
        p.via("VBAT", x, y, size=0.65, drill=0.30)
    p.route("VBAT", [p.pad_pos("C11", "2"), (35.5, 38)], width=0.24)
    p.via("VBAT", 35.5, 38, size=0.65, drill=0.30)
    for ref, via_xy in [("U2", (31.0, 28.625)), ("U3", (31.0, 50.625))]:
        p.route("VBAT", [p.pad_pos(ref, "10"), via_xy], width=0.20)
        p.via("VBAT", *via_xy, size=0.65, drill=0.30)

    # 5 V buck: the high-di/dt switch loop is short and entirely on F.Cu.
    p.route("BUCK_SW", [p.pad_pos("U1", "2"), p.pad_pos("L1", "2")], width=1.0)
    p.route("BUCK_SW", [p.pad_pos("C5", "2"), (51.2, 16.5), (51.2, 10), p.pad_pos("L1", "2")], width=0.24)
    p.route("BUCK_BOOT", [p.pad_pos("U1", "6"), (56.9, 13.5), p.pad_pos("C5", "1")], width=0.24)

    five_v_node = (43.0, 10.0)
    p.route("+5V", [p.pad_pos("L1", "1"), five_v_node], width=1.0)
    p.route("+5V", [p.pad_pos("C3", "1"), (41.0, 6.5), five_v_node], width=0.55)
    p.route("+5V", [p.pad_pos("C4", "1"), (41.0, 13.0), five_v_node], width=0.55)
    p.via("+5V", *five_v_node, size=1.0, drill=0.45)
    p.route("+5V", [five_v_node, (45, 5), (80, 5), (80, 65), p.pad_pos("C8", "1")], width=1.1, layer=pcbnew.B_Cu)
    p.route("+5V", [(80, 28), (66, 28), p.pad_pos("U4", "R1")], width=0.75, layer=pcbnew.B_Cu)
    p.route("+5V", [(80, 49.19), p.pad_pos("J6", "1")], width=0.75, layer=pcbnew.B_Cu)

    # Feedback divider and feed-forward capacitor.
    fb_node = (60.5, 8.5)
    p.route("BUCK_FB", [p.pad_pos("U1", "4"), fb_node, p.pad_pos("R1", "2")], width=0.22)
    p.route("BUCK_FB", [fb_node, p.pad_pos("R2", "1")], width=0.22)
    p.route("BUCK_FB", [p.pad_pos("R2", "1"), p.pad_pos("C6", "2")], width=0.22)
    for ref, number in [("R1", "1"), ("C6", "1")]:
        xy = p.pad_pos(ref, number)
        p.via("+5V", *xy, size=0.65, drill=0.30)
        p.route("+5V", [xy, (72, xy[1]), (72, 10), (80, 10)], width=0.28, layer=pcbnew.B_Cu)

    # UVLO/enable divider.
    en_node = p.pad_pos("R3", "2")
    p.route("BUCK_EN", [p.pad_pos("U1", "5"), (59, 10)], width=0.22)
    p.via("BUCK_EN", 59, 10, size=0.65, drill=0.30)
    p.via("BUCK_EN", *en_node, size=0.65, drill=0.30)
    p.route("BUCK_EN", [(59, 10), (70, 13), (70, 22), en_node], width=0.22, layer=pcbnew.B_Cu)
    p.route("BUCK_EN", [en_node, (61, 19.5), (61, 16.5), p.pad_pos("R4", "1")], width=0.22)

    # Motor outputs. Every 0.65 mm-pitch IC pad gets a thin escape segment
    # before the trace widens to carry motor current.
    p.route("M1_B", [p.pad_pos("U2", "2"), (20.5, 25.375)], width=0.20)
    p.route("M1_B", [(20.5, 25.375), (20.5, 18), p.pad_pos("C15", "1")], width=0.65)
    p.route("M1_B", [p.pad_pos("C15", "1"), p.pad_pos("J3", "1")], width=0.55)
    p.route("M1_A", [p.pad_pos("U2", "16"), (34, 23)], width=0.20)
    p.via("M1_A", 34, 23, size=0.65, drill=0.30)
    p.via("M1_A", *p.pad_pos("C15", "2"), size=0.65, drill=0.30)
    p.route("M1_A", [(34, 23), (35, 19), (35, 10), p.pad_pos("C15", "2"), p.pad_pos("J3", "2")], width=0.65, layer=pcbnew.B_Cu)

    p.route("M2_B", [p.pad_pos("U2", "3"), (19.5, 26.025)], width=0.20)
    p.route("M2_B", [(19.5, 26.025), (14, 28), (11, 35.5), p.pad_pos("C16", "1")], width=0.60)
    p.route("M2_B", [p.pad_pos("C16", "1"), p.pad_pos("J4", "1")], width=0.55)
    p.route("M2_A", [p.pad_pos("U2", "5"), (18.5, 27.325)], width=0.20)
    p.route("M2_A", [(18.5, 27.325), (16, 32), (11, 38.5), p.pad_pos("C16", "2")], width=0.60)
    p.route("M2_A", [p.pad_pos("C16", "2"), p.pad_pos("J4", "2")], width=0.55)

    p.route("M3_B", [p.pad_pos("U3", "2"), (20.5, 47.375)], width=0.20)
    p.route("M3_B", [(20.5, 47.375), (20.5, 56), p.pad_pos("C17", "1")], width=0.75)
    p.route("M3_B", [p.pad_pos("C17", "1"), p.pad_pos("J5", "1")], width=0.55)
    p.route("M3_A", [p.pad_pos("U3", "16"), (35, 46.725)], width=0.20)
    p.route("M3_A", [(35, 46.725), (38, 44), (39, 46), (39, 58), p.pad_pos("C17", "2")], width=0.65)
    p.route("M3_A", [p.pad_pos("C17", "2"), p.pad_pos("J5", "2")], width=0.55)

    # Current-sense resistors route on B.Cu to avoid the motor-output fanout.
    sense_links = [
        ("U2_SENSE_A", ("U2", "1"), (21.5, 24.725), ("R7", "2")),
        ("U2_SENSE_B", ("U2", "4"), (21.5, 26.675), ("R8", "2")),
        ("U3_SENSE_A", ("U3", "1"), (21.5, 46.725), ("R9", "2")),
    ]
    for net, driver_pad, escape, resistor_pad in sense_links:
        p.route(net, [p.pad_pos(*driver_pad), escape], width=0.20)
        p.via(net, *escape, size=0.65, drill=0.30)
        rxy = p.pad_pos(*resistor_pad)
        p.via(net, *rxy, size=0.65, drill=0.30)
        p.route(net, [escape, rxy], width=0.24, layer=pcbnew.B_Cu)

    # Driver charge-pump and internal-regulator capacitors.
    p.route("U2_VINT", [p.pad_pos("U2", "12"), (33, 27.325), (33, 31.5), p.pad_pos("C10", "1")], width=0.20)
    p.route("U2_VCP", [p.pad_pos("U2", "9"), p.pad_pos("C11", "1")], width=0.20)
    p.route("U3_VINT", [p.pad_pos("U3", "12"), p.pad_pos("C13", "1")], width=0.20)
    p.route("U3_VCP", [p.pad_pos("U3", "9"), p.pad_pos("C14", "1")], width=0.20)

    # Six motor-control signals transition immediately to B.Cu. Their lanes
    # stay ordered from top to bottom, so they never cross.
    signal_links = [
        ("M1_IN1", ("U4", "L2"), ("U2", "14"), (34.0, 26.025), [(35, 29), (39, 34)]),
        ("M1_IN2", ("U4", "L1"), ("U2", "13"), (36.0, 26.675), [(40, 28)]),
        ("M2_IN1", ("U4", "L3"), ("U2", "7"), (21.0, 28.625), []),
        ("M2_IN2", ("U4", "L4"), ("U2", "8"), (22.0, 30.0), []),
        ("M3_IN1", ("U4", "L5"), ("U3", "14"), (31.0, 48.025), [(36, 46)]),
        ("M3_IN2", ("U4", "L6"), ("U3", "13"), (32.0, 48.675), [(38, 48)]),
    ]
    for net, xiao_pad, driver_pad, via_xy, mids in signal_links:
        p.route(net, [p.pad_pos(*driver_pad), via_xy], width=0.20)
        p.via(net, *via_xy, size=0.65, drill=0.30)
        p.route(net, [via_xy, *mids, p.pad_pos(*xiao_pad)], width=0.22, layer=pcbnew.B_Cu)

    # Shared nSLEEP pull-up. The long connection uses In2; the surrounding
    # VBAT zone automatically clears around it.
    sleep_node = p.pad_pos("R5", "2")
    p.route("SLEEP", [p.pad_pos("U2", "15"), (31, 25.375)], width=0.20)
    p.route("SLEEP", [p.pad_pos("U3", "15"), (36, 47.375)], width=0.20)
    p.via("SLEEP", 31, 25.375, size=0.65, drill=0.30)
    p.via("SLEEP", 36, 47.375, size=0.65, drill=0.30)
    p.via("SLEEP", *sleep_node, size=0.65, drill=0.30)
    p.route("SLEEP", [(31, 25.375), (41, 20), (45.5, 20), sleep_node], width=0.22, layer=pcbnew.In2_Cu)
    p.route("SLEEP", [(36, 47.375), (41, 50), (41, 20)], width=0.22, layer=pcbnew.In2_Cu)
    p.route("+3V3", [p.pad_pos("U4", "R3"), (62.5, 38.46), (62.5, 22), (40, 22), p.pad_pos("R5", "1")], width=0.26)
    p.route("+3V3", [p.pad_pos("U4", "R3"), (74, 38.46), (82, 32.46), p.pad_pos("J7", "1")], width=0.30)
    p.route("TOUCH", [p.pad_pos("U4", "R4"), (76, 41), (82, 37.54), p.pad_pos("J7", "3")], width=0.24)

    # UART to the upper body. TX changes layer before the right-side 5 V trunk.
    p.route("UART_TX", [p.pad_pos("U4", "L7"), (55, 55), (76, 54.27)], width=0.26, layer=pcbnew.B_Cu)
    p.via("UART_TX", 76, 54.27, size=0.65, drill=0.30)
    p.route("UART_TX", [(76, 54.27), p.pad_pos("J6", "3")], width=0.26)
    p.route("UART_RX", [p.pad_pos("U4", "R7"), (70, 62), (74, 68), (90, 68), (90, 56.81), p.pad_pos("J6", "4")], width=0.26)

    # Ground access. PTH connector grounds connect directly to the In1 plane.
    for x, y in [(26, 27), (26, 49)]:
        p.via("GND", x, y, size=0.75, drill=0.30)
    for ref in ("U2", "U3"):
        x, y = p.pad_pos(ref, "11")
        p.route("GND", [(x, y), (31.7, y)], width=0.20)
        p.via("GND", 31.7, y, size=0.65, drill=0.30)
    p.route("GND", [p.pad_pos("U3", "4"), (26, 49)], width=0.20)

    ground_pads = [
        ("D1", "2"), ("C1", "1"), ("C2", "1"),
        ("C3", "2"), ("C4", "2"), ("R2", "2"), ("R4", "2"),
        ("U1", "1"), ("C9", "2"), ("C10", "2"), ("C12", "2"),
        ("C13", "2"), ("R7", "1"), ("R8", "1"), ("R9", "1"),
    ]
    for ref, number in ground_pads:
        x, y = p.pad_pos(ref, number)
        p.via("GND", x, y, size=0.65, drill=0.30)


def write_project_files(p: PCB) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    pcbnew.ZONE_FILLER(p.board).Fill(p.board.Zones())
    pcbnew.SaveBoard(str(BOARD_PATH), p.board)

    project = {
        "board": {},
        "boards": [],
        "cvpcb": {},
        "erc": {},
        "libraries": {},
        "meta": {"filename": f"{PROJECT}.kicad_pro", "version": 1},
        "net_settings": {},
        "pcbnew": {},
        "schematic": {},
        "text_variables": {},
    }
    (ROOT / f"{PROJECT}.kicad_pro").write_text(
        json.dumps(project, indent=2),
        encoding="utf-8",
    )

    with (ROOT / "BOM.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Reference", "Value", "Footprint", "Description", "LCSC", "Assembly"])
        for component in COMPONENTS:
            writer.writerow([
                component.ref,
                component.value,
                component.footprint,
                component.description,
                component.lcsc,
                component.assembly,
            ])

    pinout = [
        ("J1", "1", "BAT_RAW", "2S protected battery positive"),
        ("J1", "2", "GND", "Battery negative"),
        ("J2", "1", "BAT_FUSED", "To external latching switch"),
        ("J2", "2", "VBAT", "Switched return; jumper J2 for bench test"),
        ("J3", "1/2", "M1_B/M1_A", "N20 motor 1"),
        ("J4", "1/2", "M2_B/M2_A", "N20 motor 2"),
        ("J5", "1/2", "M3_B/M3_A", "N20 motor 3"),
        ("J6", "1", "+5V", "5V up to 3A for current robot"),
        ("J6", "2", "GND", "Common ground"),
        ("J6", "3", "UART_TX", "Base TX to upper RX"),
        ("J6", "4", "UART_RX", "Base RX from upper TX"),
        ("J7", "1", "+3V3", "Touch module power"),
        ("J7", "2", "GND", "Touch module ground"),
        ("J7", "3", "TOUCH", "Digital touch output to XIAO D10"),
    ]
    with (ROOT / "pinout.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Connector", "Pin", "Net", "Description"])
        writer.writerows(pinout)

    mcu_pinout = [
        ("D0", "M1_IN2", "Motor 1 direction/PWM B"),
        ("D1", "M1_IN1", "Motor 1 direction/PWM A"),
        ("D2", "M2_IN1", "Motor 2 direction/PWM A"),
        ("D3", "M2_IN2", "Motor 2 direction/PWM B"),
        ("D4", "M3_IN1", "Motor 3 direction/PWM A"),
        ("D5", "M3_IN2", "Motor 3 direction/PWM B"),
        ("D6", "UART_TX", "Base TX to upper-body RX"),
        ("D7", "UART_RX", "Base RX from upper-body TX"),
        ("D8", "SPARE", "Reserved"),
        ("D9", "SPARE", "Reserved"),
        ("D10", "TOUCH", "Digital touch input"),
    ]
    with (ROOT / "mcu_pinout.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["XIAO Pin", "Net", "Description"])
        writer.writerows(mcu_pinout)

    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720">
<style>
text{font-family:Segoe UI,Arial,sans-serif;fill:#18202a}.title{font-size:30px;font-weight:700}
.box{fill:#eef3f8;stroke:#315b7d;stroke-width:3}.power{fill:#fff2db;stroke:#b97113}
.motor{fill:#eef8ee;stroke:#2f7a43}.wire{fill:none;stroke:#27384a;stroke-width:4}
.pwr{stroke:#c5492d}.label{font-size:18px;font-weight:600}.small{font-size:15px}
</style>
<text x="40" y="48" class="title">Brufik N3 Mobile Base Controller — Rev 0.1</text>
<rect x="40" y="110" width="170" height="90" rx="16" class="box power"/>
<text x="72" y="145" class="label">2S Battery</text><text x="62" y="176" class="small">Fuse + switch + TVS</text>
<rect x="290" y="80" width="190" height="120" rx="16" class="box power"/>
<text x="326" y="120" class="label">TPS54302</text><text x="316" y="154" class="small">5V / 3A upper rail</text>
<rect x="290" y="260" width="220" height="150" rx="16" class="box"/>
<text x="326" y="302" class="label">XIAO ESP32-C3</text>
<text x="312" y="336" class="small">3-wheel kinematics</text><text x="312" y="365" class="small">UART + touch + sleep</text>
<rect x="620" y="210" width="210" height="100" rx="16" class="box motor"/>
<text x="656" y="252" class="label">DRV8833 #1</text><text x="650" y="284" class="small">Motor 1 + Motor 2</text>
<rect x="620" y="360" width="210" height="100" rx="16" class="box motor"/>
<text x="656" y="402" class="label">DRV8833 #2</text><text x="662" y="434" class="small">Motor 3 + spare</text>
<rect x="925" y="165" width="210" height="90" rx="16" class="box motor"/>
<text x="980" y="218" class="label">N20 M1</text>
<rect x="925" y="290" width="210" height="90" rx="16" class="box motor"/>
<text x="980" y="343" class="label">N20 M2</text>
<rect x="925" y="415" width="210" height="90" rx="16" class="box motor"/>
<text x="980" y="468" class="label">N20 M3</text>
<rect x="620" y="550" width="210" height="90" rx="16" class="box"/>
<text x="665" y="590" class="label">Touch Module</text><text x="674" y="618" class="small">TTP223 / digital</text>
<rect x="290" y="550" width="220" height="90" rx="16" class="box"/>
<text x="334" y="590" class="label">Upper Robot</text><text x="317" y="618" class="small">5V + GND + UART</text>
<path d="M210 140 H290" class="wire pwr"/><path d="M210 175 H250 V235 H620" class="wire pwr"/>
<path d="M385 200 V260" class="wire pwr"/><path d="M480 140 H550 V595 H510" class="wire pwr"/>
<path d="M510 305 H620" class="wire"/><path d="M510 370 H620" class="wire"/>
<path d="M830 240 H880 V210 H925" class="wire"/><path d="M830 270 H890 V335 H925" class="wire"/>
<path d="M830 410 H890 V460 H925" class="wire"/><path d="M510 595 H620" class="wire"/>
<path d="M400 410 V550" class="wire"/>
<text x="230" y="130" class="small">VBAT</text><text x="520" y="224" class="small">VBAT</text>
<text x="520" y="295" class="small">6 PWM</text><text x="505" y="583" class="small">UART</text>
</svg>"""
    (ROOT / "schematic_block.svg").write_text(svg, encoding="utf-8")


def main() -> None:
    pcb = PCB()
    populate(pcb)
    route_board(pcb)
    write_project_files(pcb)


if __name__ == "__main__":
    main()

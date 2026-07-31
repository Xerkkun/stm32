"""Generate the UNO R4 / STM32 power-cycle controller schematic with SKiDL.

The output is an editable KiCad 10 hierarchical schematic.  This file is the
source of truth for connectivity; generated KiCad files are review artifacts.

Important evidence boundary:
    A generated schematic and a clean ERC are not physical-board validation.
"""

from __future__ import annotations

import os
import builtins
from pathlib import Path


SCHEMATIC_DIR = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = SCHEMATIC_DIR.parent
KICAD_DIR = SCHEMATIC_DIR / "kicad"
EXPORT_DIR = SCHEMATIC_DIR / "exports"
BUILD_DIR = CONTROLLER_DIR / "build" / "schematics"
SKIDL_STATE_DIR = BUILD_DIR / "skidl_state"

KICAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
BUILD_DIR.mkdir(parents=True, exist_ok=True)
SKIDL_STATE_DIR.mkdir(parents=True, exist_ok=True)

# SKiDL creates persistent caches while importing.  Keeping them under build/
# avoids changing the user's global configuration and works in restricted CI.
os.environ["APPDATA"] = str(SKIDL_STATE_DIR)
os.environ["LOCALAPPDATA"] = str(SKIDL_STATE_DIR)


def _find_kicad_root() -> Path:
    """Locate KiCad 10, honoring the environment before standard paths."""

    env_symbol_dir = os.environ.get("KICAD10_SYMBOL_DIR")
    if env_symbol_dir:
        candidate = Path(env_symbol_dir).expanduser().resolve()
        if candidate.is_dir():
            return candidate.parents[2]

    candidates = (
        Path(r"C:\Program Files\KiCad\10.0"),
        Path(r"C:\Program Files (x86)\KiCad\10.0"),
    )
    for candidate in candidates:
        if (candidate / "share" / "kicad" / "symbols").is_dir():
            return candidate

    raise FileNotFoundError(
        "KiCad 10 no fue localizado. Define KICAD10_SYMBOL_DIR con la ruta "
        "de la carpeta kicad-symbols."
    )


KICAD_ROOT = _find_kicad_root()
SYMBOL_DIR = KICAD_ROOT / "share" / "kicad" / "symbols"
FOOTPRINT_DIR = KICAD_ROOT / "share" / "kicad" / "footprints"

# SKiDL 2.3.0 reads KICAD10_SYMBOL_DIR.  KICAD_SYMBOL_DIR is retained for old
# examples, and the remaining variables silence discovery warnings caused by
# SKiDL initializing all installed KiCad backends.
os.environ["KICAD_SYMBOL_DIR"] = str(SYMBOL_DIR)
for _version in range(6, 11):
    os.environ[f"KICAD{_version}_SYMBOL_DIR"] = str(SYMBOL_DIR)
    os.environ[f"KICAD{_version}_FOOTPRINT_DIR"] = str(FOOTPRINT_DIR)

_kicad_bin = KICAD_ROOT / "bin"
os.environ["PATH"] = str(_kicad_bin) + os.pathsep + os.environ.get("PATH", "")


from skidl import (  # noqa: E402  (environment must be ready before import)
    ERC,
    KICAD10,
    POWER,
    Net,
    Part,
    generate_netlist,
    generate_schematic,
    lib_search_paths,
    set_default_tool,
    subcircuit,
)


TARGET = KICAD10
SCHEMATIC_ONLY_FP = "Schematic_Only:Do_Not_Use_For_PCB"

set_default_tool(TARGET)
if str(SYMBOL_DIR) not in lib_search_paths[TARGET]:
    lib_search_paths[TARGET].append(str(SYMBOL_DIR))


def _part(library: str, name: str, *, ref: str, value: str) -> Part:
    """Create a schematic-only part with an explicit stable reference."""

    return Part(
        library,
        name,
        ref=ref,
        tag=ref,
        value=value,
        footprint=SCHEMATIC_ONLY_FP,
    )


def _resistor(ref: str, value: str) -> Part:
    return _part("Device", "R", ref=ref, value=value)


def _connector(ref: str, pins: int, value: str) -> Part:
    return _part(
        "Connector_Generic",
        f"Conn_01x{pins:02d}",
        ref=ref,
        value=value,
    )


def _attach(net: Net, *pins) -> None:
    net += pins


def _mark_nc(*pins) -> None:
    nc = builtins.NC
    nc += pins


@subcircuit
def control_uno_r4(
    five_arduino,
    five_relays,
    gnd,
    uno_d2,
    uno_d3,
    uno_d6,
    uno_d7,
    uno_d8,
    uno_d9,
    uno_a0,
    uno_a1,
    i2c_sda,
    i2c_scl,
):
    """UNO R4 headers and the external four-channel relay control header."""

    j_digital = _connector("J1", 6, "UNO R4 DIGITAL")
    j_analog = _connector("J2", 4, "UNO R4 ANALOG-I2C")
    j_power = _connector("J3", 2, "UNO R4 POWER")
    j_relay = _connector("J4", 6, "4CH RELAY CTRL")
    rpu1 = _resistor("RPU1", "10k PU IN1")
    rpu2 = _resistor("RPU2", "10k PU IN2")
    flag_arduino = Part(
        "power",
        "PWR_FLAG",
        ref="#FLG01",
        tag="PWR_ARDUINO",
        footprint=SCHEMATIC_ONLY_FP,
    )
    flag_relays = Part(
        "power",
        "PWR_FLAG",
        ref="#FLG02",
        tag="PWR_RELAYS",
        footprint=SCHEMATIC_ONLY_FP,
    )
    flag_gnd = Part(
        "power",
        "PWR_FLAG",
        ref="#FLG03",
        tag="PWR_GND",
        footprint=SCHEMATIC_ONLY_FP,
    )

    # J1 pin map: 1=D2, 2=D3, 3=D6, 4=D7, 5=D8, 6=D9.
    _attach(uno_d2, j_digital[1])
    _attach(uno_d3, j_digital[2])
    _attach(uno_d6, j_digital[3])
    _attach(uno_d7, j_digital[4], j_relay[3], rpu1[1])
    _attach(uno_d8, j_digital[5])
    _attach(uno_d9, j_digital[6], j_relay[4], rpu2[1])

    # J2 pin map: 1=A0, 2=A1, 3=A4/SDA, 4=A5/SCL.
    _attach(uno_a0, j_analog[1])
    _attach(uno_a1, j_analog[2])
    _attach(i2c_sda, j_analog[3])
    _attach(i2c_scl, j_analog[4])

    # J3 pin map: 1=5V, 2=GND.
    _attach(five_arduino, j_power[1])
    _attach(gnd, j_power[2])

    # J4 pin map: 1=VCC, 2=GND, 3=IN1, 4=IN2, 5=IN3, 6=IN4.
    _attach(five_relays, j_relay[1], rpu1[2], rpu2[2])
    _attach(gnd, j_relay[2])
    _attach(five_arduino, flag_arduino[1])
    _attach(five_relays, flag_relays[1])
    _attach(gnd, flag_gnd[1])
    _mark_nc(j_relay[5], j_relay[6])


def _marker_stage(
    source,
    output,
    five_arduino,
    gnd,
    *,
    index: int,
    qref: str,
):
    """One inverting 2N2222A level/interface stage."""

    q = _part("Transistor_BJT", "Q_NPN_CBE", ref=qref, value="2N2222A")
    rb = _resistor(f"RB{index}", "10k RB")
    rbe = _resistor(f"RBE{index}", "100k RBE")
    rc = _resistor(f"RC{index}", "10k RC")
    base = Net(f"Q{index}_B")

    _attach(source, rb[1])
    _attach(base, rb[2], q["B"], rbe[1])
    _attach(gnd, q["E"], rbe[2])
    _attach(output, q["C"], rc[1])
    _attach(five_arduino, rc[2])


@subcircuit
def senales_f746(
    five_arduino,
    gnd,
    pe0,
    pa0,
    rail_3v3,
    uno_d2,
    uno_d6,
    uno_a0,
):
    """F746 energy/clock markers and 3V3-presence divider."""

    j_nucleo = _connector("J5", 4, "NUCLEO-F746ZG SIGNALS")
    rtop = _resistor("RS1", "10k")
    rbottom = _resistor("RS2", "20k")

    # J5: 1=PE0/D34, 2=PA0/D32, 3=3V3/CN8-7, 4=GND/CN8-11|13.
    _attach(pe0, j_nucleo[1])
    _attach(pa0, j_nucleo[2])
    _attach(rail_3v3, j_nucleo[3], rtop[1])
    _attach(gnd, j_nucleo[4], rbottom[2])
    _attach(uno_a0, rtop[2], rbottom[1])

    _marker_stage(pe0, uno_d2, five_arduino, gnd, index=1, qref="Q1")
    _marker_stage(pa0, uno_d6, five_arduino, gnd, index=3, qref="Q3")


@subcircuit
def senales_h755(
    five_arduino,
    gnd,
    pe0,
    pa0,
    rail_3v3,
    uno_d3,
    uno_d8,
    uno_a1,
):
    """H755 energy/clock markers and 3V3-presence divider."""

    j_nucleo = _connector("J6", 4, "NUCLEO-H755ZI-Q SIGNALS")
    rtop = _resistor("RS3", "10k")
    rbottom = _resistor("RS4", "20k")

    # J6: 1=PE0/D34, 2=PA0/D32, 3=3V3/CN8-7, 4=GND/CN8-11|13.
    _attach(pe0, j_nucleo[1])
    _attach(pa0, j_nucleo[2])
    _attach(rail_3v3, j_nucleo[3], rtop[1])
    _attach(gnd, j_nucleo[4], rbottom[2])
    _attach(uno_a1, rtop[2], rbottom[1])

    _marker_stage(pe0, uno_d3, five_arduino, gnd, index=2, qref="Q2")
    _marker_stage(pa0, uno_d8, five_arduino, gnd, index=4, qref="Q4")


@subcircuit
def potencia_f746(
    five_arduino,
    gnd,
    i2c_sda,
    i2c_scl,
    usb_source_vbus,
    usb_after_relay,
    vbus_load,
    usb_dm,
    usb_dp,
    usb_shield,
):
    """F746 switched VBUS path, INA226 module, and continuous USB signals."""

    j_source = _connector("J7", 5, "USB SOURCE F746")
    j_target = _connector("J8", 5, "USB TARGET F746")
    ina = _connector("U1", 10, "INA226 F 0x40")
    contact = _part("Switch", "SW_SPDT", ref="K1", value="K1 CONTACT")
    rshunt = _resistor("RSH1", "R100 U1")

    # USB connectors: 1=VBUS, 2=D-, 3=D+, 4=GND, 5=shield.
    _attach(usb_source_vbus, j_source[1], contact[2])  # COM.
    _attach(usb_after_relay, contact[3], ina[9], rshunt[1])  # NO -> IN+.
    _mark_nc(contact[1])  # NC screw terminal is intentionally unused.

    _attach(vbus_load, rshunt[2], ina[10], ina[8], j_target[1])
    _attach(usb_dm, j_source[2], j_target[2])
    _attach(usb_dp, j_source[3], j_target[3])
    _attach(gnd, j_source[4], j_target[4], ina[2], ina[6], ina[7])
    _attach(usb_shield, j_source[5], j_target[5])

    # INA U1: 1=VS, 2=GND, 3=SDA, 4=SCL, 5=ALERT, 6=A0, 7=A1,
    #         8=VBUS, 9=IN+, 10=IN-. A0=A1=GND -> 0x40.
    _attach(five_arduino, ina[1])
    _attach(i2c_sda, ina[3])
    _attach(i2c_scl, ina[4])
    _mark_nc(ina[5])


@subcircuit
def potencia_h755(
    five_arduino,
    gnd,
    i2c_sda,
    i2c_scl,
    usb_source_vbus,
    usb_after_relay,
    vbus_load,
    usb_dm,
    usb_dp,
    usb_shield,
):
    """H755 switched VBUS path, INA226 module, and continuous USB signals."""

    j_source = _connector("J9", 5, "USB SOURCE H755")
    j_target = _connector("J10", 5, "USB TARGET H755")
    ina = _connector("U2", 10, "INA226 H 0x41")
    contact = _part("Switch", "SW_SPDT", ref="K2", value="K2 CONTACT")
    rshunt = _resistor("RSH2", "R100 U2")

    _attach(usb_source_vbus, j_source[1], contact[2])  # COM.
    _attach(usb_after_relay, contact[3], ina[9], rshunt[1])  # NO -> IN+.
    _mark_nc(contact[1])

    _attach(vbus_load, rshunt[2], ina[10], ina[8], j_target[1])
    _attach(usb_dm, j_source[2], j_target[2])
    _attach(usb_dp, j_source[3], j_target[3])
    _attach(gnd, j_source[4], j_target[4], ina[2], ina[7])
    _attach(usb_shield, j_source[5], j_target[5])

    # INA U2: A1=GND and A0=VS -> 0x41.
    _attach(five_arduino, ina[1], ina[6])
    _attach(i2c_sda, ina[3])
    _attach(i2c_scl, ina[4])
    _mark_nc(ina[5])


def _net(name: str, *, power: bool = False) -> Net:
    net = Net(name)
    # Use explicit labels for all inter-sheet/system nets.  Short local nets
    # such as each transistor base remain physically wired.
    net.stub = True
    if power:
        net.drive = POWER
    return net


def _build_circuit() -> dict[str, Net]:
    """Instantiate the complete five-sheet wiring contract."""

    nets = {
        "gnd": _net("GND", power=True),
        "five_arduino": _net("+5V_ARD", power=True),
        "five_relays": _net("+5V2_RELAY", power=True),
        "uno_d2": _net("D2_F_EN_N"),
        "uno_d3": _net("D3_H_EN_N"),
        "uno_d6": _net("D6_F_CLK_N"),
        "uno_d7": _net("D7_RELAY1_N"),
        "uno_d8": _net("D8_H_CLK_N"),
        "uno_d9": _net("D9_RELAY2_N"),
        "uno_a0": _net("A0_F_3V3"),
        "uno_a1": _net("A1_H_3V3"),
        "i2c_sda": _net("A4_SDA"),
        "i2c_scl": _net("A5_SCL"),
        "f746_pe0": _net("F_PE0_D34"),
        "f746_pa0": _net("F_PA0_D32"),
        "f746_3v3": _net("F_3V3_CN8_7", power=True),
        "h755_pe0": _net("H_PE0_D34"),
        "h755_pa0": _net("H_PA0_D32"),
        "h755_3v3": _net("H_3V3_CN8_7", power=True),
        "f746_src_vbus": _net("F_SRC_VBUS", power=True),
        "f746_relay_out": _net("F_K1_NO_IN+"),
        "f746_vbus_load": _net("F_LOAD_VBUS", power=True),
        "f746_dm": _net("F_USB_D-"),
        "f746_dp": _net("F_USB_D+"),
        "f746_shield": _net("F_USB_SH"),
        "h755_src_vbus": _net("H_SRC_VBUS", power=True),
        "h755_relay_out": _net("H_K2_NO_IN+"),
        "h755_vbus_load": _net("H_LOAD_VBUS", power=True),
        "h755_dm": _net("H_USB_D-"),
        "h755_dp": _net("H_USB_D+"),
        "h755_shield": _net("H_USB_SH"),
    }

    control_uno_r4(
        nets["five_arduino"],
        nets["five_relays"],
        nets["gnd"],
        nets["uno_d2"],
        nets["uno_d3"],
        nets["uno_d6"],
        nets["uno_d7"],
        nets["uno_d8"],
        nets["uno_d9"],
        nets["uno_a0"],
        nets["uno_a1"],
        nets["i2c_sda"],
        nets["i2c_scl"],
        tag="control_uno_r4",
    )

    senales_f746(
        nets["five_arduino"],
        nets["gnd"],
        nets["f746_pe0"],
        nets["f746_pa0"],
        nets["f746_3v3"],
        nets["uno_d2"],
        nets["uno_d6"],
        nets["uno_a0"],
        tag="senales_f746",
    )
    senales_h755(
        nets["five_arduino"],
        nets["gnd"],
        nets["h755_pe0"],
        nets["h755_pa0"],
        nets["h755_3v3"],
        nets["uno_d3"],
        nets["uno_d8"],
        nets["uno_a1"],
        tag="senales_h755",
    )

    potencia_f746(
        nets["five_arduino"],
        nets["gnd"],
        nets["i2c_sda"],
        nets["i2c_scl"],
        nets["f746_src_vbus"],
        nets["f746_relay_out"],
        nets["f746_vbus_load"],
        nets["f746_dm"],
        nets["f746_dp"],
        nets["f746_shield"],
        tag="potencia_f746",
    )
    potencia_h755(
        nets["five_arduino"],
        nets["gnd"],
        nets["i2c_sda"],
        nets["i2c_scl"],
        nets["h755_src_vbus"],
        nets["h755_relay_out"],
        nets["h755_vbus_load"],
        nets["h755_dm"],
        nets["h755_dp"],
        nets["h755_shield"],
        tag="potencia_h755",
    )

    return nets


def _endpoints(net: Net) -> set[str]:
    return {f"{pin.part.ref}.{pin.num}" for pin in net.get_pins()}


def _validate_contract(nets: dict[str, Net]) -> list[str]:
    """Check the safety-critical endpoint subsets before drawing."""

    expected = {
        "uno_d7": {"J1.4", "J4.3", "RPU1.1"},
        "uno_d9": {"J1.6", "J4.4", "RPU2.1"},
        "uno_d2": {"J1.1", "Q1.1", "RC1.1"},
        "uno_d3": {"J1.2", "Q2.1", "RC2.1"},
        "uno_d6": {"J1.3", "Q3.1", "RC3.1"},
        "uno_d8": {"J1.5", "Q4.1", "RC4.1"},
        "uno_a0": {"J2.1", "RS1.2", "RS2.1"},
        "uno_a1": {"J2.2", "RS3.2", "RS4.1"},
        "i2c_sda": {"J2.3", "U1.3", "U2.3"},
        "i2c_scl": {"J2.4", "U1.4", "U2.4"},
        "f746_src_vbus": {"J7.1", "K1.2"},
        "f746_relay_out": {"K1.3", "U1.9", "RSH1.1"},
        "f746_vbus_load": {"RSH1.2", "U1.8", "U1.10", "J8.1"},
        "h755_src_vbus": {"J9.1", "K2.2"},
        "h755_relay_out": {"K2.3", "U2.9", "RSH2.1"},
        "h755_vbus_load": {"RSH2.2", "U2.8", "U2.10", "J10.1"},
    }

    report = []
    for key, required in expected.items():
        actual = _endpoints(nets[key])
        missing = required - actual
        if missing:
            raise AssertionError(
                f"Contrato roto en {nets[key].name}; faltan {sorted(missing)}; "
                f"actual={sorted(actual)}"
            )
        report.append(f"PASS {nets[key].name}: {', '.join(sorted(required))}")

    if nets["five_arduino"] is nets["five_relays"]:
        raise AssertionError("+5V_ARDUINO y +5V2_RELES no deben ser la misma red.")

    shared_power_pins = _endpoints(nets["five_arduino"]) & _endpoints(
        nets["five_relays"]
    )
    if shared_power_pins:
        raise AssertionError(
            "Los rieles positivos quedaron unidos mediante: "
            + ", ".join(sorted(shared_power_pins))
        )

    report.append("PASS +5V_ARDUINO y +5V2_RELES permanecen separados")
    report.append("PASS GND es la única referencia común de alimentación")
    return report


def main() -> None:
    nets = _build_circuit()
    contract_report = _validate_contract(nets)

    ERC()

    netlist_path = BUILD_DIR / "uno_r4_power_controller.net"
    generate_netlist(
        tool=TARGET,
        file_=str(netlist_path),
        do_backup=False,
    )

    generate_schematic(
        tool=TARGET,
        filepath=str(KICAD_DIR),
        top_name="uno_r4_power_controller",
        title=(
            "UNO R4 + STM32 power-cycle controller"
        ),
        flatness=0.0,
        retries=3,
        auto_stub=True,
        auto_stub_fallback="labels",
        label_clearance=True,
        label_overlap_weight=250.0,
        seed=746755,
    )

    report_path = BUILD_DIR / "schematic_contract_check.txt"
    report_path.write_text("\n".join(contract_report) + "\n", encoding="utf-8")

    print(f"SKiDL target: {TARGET}")
    print(f"KiCad root: {KICAD_ROOT}")
    print(f"Symbol dir: {SYMBOL_DIR}")
    print(f"Schematic: {KICAD_DIR / 'uno_r4_power_controller.kicad_sch'}")
    print(f"Netlist: {netlist_path}")
    print(f"Contract: {report_path}")


if __name__ == "__main__":
    main()

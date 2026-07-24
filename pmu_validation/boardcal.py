"""Provision the bench calibration into the micro-PMU's own flash.

The board's USB protocol is stream-only (no host->device channel), so the
calibrated ``volts_per_count`` can't be sent over the CDC link. Instead it is
written into user-flash **sector 7 (0x080E0000)** through the Nucleo's on-board
ST-LINK with ``STM32_Programmer_CLI``; the firmware validates the record at
that address and reports the value in every STATUS frame (protocol v4,
``cal_volts_per_count``), so any host that plugs into the deployed board picks
up its factory calibration automatically.

Record layout (16 bytes, little-endian) -- must match
``apps/power-brick/firmware/App/calstore.h``:

    offset size field
    0      4    magic            = 0x314C4143 ("CAL1")
    4      4    volts_per_count  (IEEE-754 f32)
    8      4    reserved         = 0xFFFFFFFF
    12     2    crc16            CRC-16/CCITT-FALSE over bytes [0..11]
    14     2    pad              = 0xFFFF
"""
from __future__ import annotations

import glob
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ._vendor import import_upmu

CAL_ADDR = 0x080E0000
CAL_SECTOR = 7
CAL_MAGIC = 0x314C4143  # "CAL1"

# Common install locations for STM32_Programmer_CLI when it isn't on PATH.
_PROGRAMMER_GLOBS = [
    r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
    r"C:\Program Files (x86)\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
    r"C:\ST\STM32CubeCLT*\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
    r"C:\ST\STM32CubeIDE*\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.*\tools\bin\STM32_Programmer_CLI.exe",
]

# Fallback flasher: open-source st-flash (stlink-org). It erases the touched
# sector automatically before writing, so the flow is a single command.
_STFLASH_GLOBS = [
    r"C:\Program Files*\stlink\bin\st-flash.exe",
    r"C:\Users\*\stm32-tools\stlink\*\bin\st-flash.exe",
]


def build_record(volts_per_count: float) -> bytes:
    """Serialize the 16-byte calibration record (see module docstring)."""
    if not (0.0 < volts_per_count < 1.0):
        raise ValueError(f"implausible volts_per_count: {volts_per_count!r}")
    import_upmu()                      # puts the vendored host pkg on sys.path
    from upmu.crc import crc16, INIT   # same CRC-16/CCITT-FALSE as the wire
    body = struct.pack("<IfI", CAL_MAGIC, volts_per_count, 0xFFFFFFFF)
    return body + struct.pack("<HH", crc16(body, INIT), 0xFFFF)


def _first_glob(patterns: list[str]) -> str | None:
    for pattern in patterns:
        hits = sorted(glob.glob(pattern), reverse=True)  # newest version first
        if hits:
            return hits[0]
    return None


def find_programmer() -> str | None:
    """Locate STM32_Programmer_CLI (PATH first, then standard ST installs)."""
    return shutil.which("STM32_Programmer_CLI") or _first_glob(_PROGRAMMER_GLOBS)


def find_stflash() -> str | None:
    """Locate the open-source ``st-flash`` fallback."""
    return shutil.which("st-flash") or _first_glob(_STFLASH_GLOBS)


def _run(cmd: list[str], timeout_s: float, tool: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-12:])
        raise RuntimeError(f"{tool} failed (exit {proc.returncode}):\n{tail}")
    return out


def write_to_board(volts_per_count: float, *, programmer: str | None = None,
                   timeout_s: float = 120.0) -> str:
    """Erase sector 7, write the record, verify, and reset the board.

    Uses STM32_Programmer_CLI when available, otherwise the open-source
    ``st-flash`` (which erases the touched sector itself). Returns the tool's
    output on success; raises ``RuntimeError`` with the output tail on any
    failure. The board hardware-resets at the end, so an open CDC stream will
    drop and re-enumerate -- reconnect afterwards.
    """
    record = build_record(volts_per_count)
    with tempfile.TemporaryDirectory(prefix="upmu_cal_") as td:
        binf = Path(td) / "cal_record.bin"
        binf.write_bytes(record)
        exe = programmer or find_programmer()
        if exe:
            cmd = [exe, "-c", "port=SWD", "mode=UR",
                   "--erase", str(CAL_SECTOR),
                   "-d", str(binf), f"0x{CAL_ADDR:08X}",
                   "-v", "-rst"]
            return _run(cmd, timeout_s, "STM32_Programmer_CLI")
        stf = find_stflash()
        if stf:
            cmd = [stf, "--reset", "write", str(binf), f"0x{CAL_ADDR:08X}"]
            return _run(cmd, timeout_s, "st-flash")
    raise RuntimeError(
        "No flasher found: install STM32CubeProgrammer (STM32_Programmer_CLI) "
        "or the open-source stlink tools (st-flash) and ensure one is on PATH "
        "-- either drives the Nucleo's on-board ST-LINK that writes the "
        "calibration record.")

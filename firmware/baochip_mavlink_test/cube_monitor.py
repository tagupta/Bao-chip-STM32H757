#!/usr/bin/env python3
"""Minimal ground station for the Baochip -> Cube Orange+ relay test.

Connects to the Cube over its micro-USB port and:
  1. reports which firmware it runs (ArduCopter, ArduPlane, PX4, ...),
  2. reads SERIAL1_PROTOCOL / SERIAL1_BAUD (TELEM1) and, with --fix, sets
     them to MAVLink2 / 57600,
  3. watches for the Baochip's heartbeat (sysid 42, forwarded from TELEM1),
     flight-mode changes, and the Cube's status messages.

  .venv/bin/python firmware/baochip_mavlink_test/cube_monitor.py
  .venv/bin/python firmware/baochip_mavlink_test/cube_monitor.py --port /dev/cu.usbmodem1101 --fix
"""
import argparse
import glob
import sys
import time

from pymavlink import mavutil

BAOCHIP_SYSID = 42
AUTOPILOTS = {3: "ArduPilot", 12: "PX4"}
ARDUPILOT_VEHICLES = {
    1: "ArduPlane", 2: "ArduCopter", 3: "ArduCopter", 4: "ArduCopter", 10: "Rover",
    12: "ArduSub", 13: "ArduCopter", 14: "ArduCopter", 15: "ArduCopter",
}
WANT = {"SERIAL1_PROTOCOL": 2, "SERIAL1_BAUD": 57}


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("No /dev/cu.usbmodem* found. Is the Cube's micro-USB plugged in with a data cable?")
    if len(ports) > 1:
        sys.exit(f"Several USB serial ports found, pick one with --port: {', '.join(ports)}")
    return ports[0]


def wait_autopilot_heartbeat(m, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb and hb.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            return hb
    return None


def firmware_version(m):
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 0,
                            mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION, 0, 0, 0, 0, 0, 0)
    msg = m.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=3)
    if not msg:
        return "unknown"
    v = msg.flight_sw_version
    return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}"


def read_param(m, name):
    for _ in range(3):
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        deadline = time.time() + 2
        while time.time() < deadline:
            p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if p and p.param_id == name:
                return int(p.param_value)
    return None


def set_param(m, name, value):
    m.mav.param_set_send(m.target_system, m.target_component, name.encode(), float(value),
                         mavutil.mavlink.MAV_PARAM_TYPE_INT8)
    time.sleep(0.5)
    return read_param(m, name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="Cube USB serial port (default: the only /dev/cu.usbmodem*)")
    ap.add_argument("--fix", action="store_true", help="set SERIAL1_PROTOCOL=2 and SERIAL1_BAUD=57")
    args = ap.parse_args()

    port = args.port or find_port()
    print(f"Connecting to {port} ...")
    m = mavutil.mavlink_connection(port, baud=115200, source_system=255)

    hb = wait_autopilot_heartbeat(m, 15)
    if not hb:
        sys.exit("No heartbeat from the Cube in 15 s. It may only have a bootloader (no flight firmware), "
                 "or the port is not the Cube.")
    m.target_system, m.target_component = hb.get_srcSystem(), hb.get_srcComponent()

    autopilot = AUTOPILOTS.get(hb.autopilot, f"autopilot id {hb.autopilot}")
    vehicle = ARDUPILOT_VEHICLES.get(hb.type, f"vehicle type {hb.type}") if hb.autopilot == 3 else ""
    print(f"Firmware: {autopilot} {vehicle} {firmware_version(m)}  (system {m.target_system})")
    if hb.autopilot != 3:
        sys.exit("This is not ArduPilot. The relay test needs ArduCopter on the Cube.")
    if vehicle != "ArduCopter":
        print("Warning: not ArduCopter, so mode numbers differ (LOITER=5 is a Copter number).")

    ok = True
    for name, want in WANT.items():
        have = read_param(m, name)
        if have != want and args.fix:
            have = set_param(m, name, want)
            print(f"{name} set to {have}. Power-cycle the Cube for TELEM1 to pick it up.")
        print(f"{name} = {have}  (want {want}){'' if have == want else '  <-- MISMATCH'}")
        ok &= have == want
    if not ok and not args.fix:
        print("Run again with --fix to correct these.")

    modes = mavutil.mode_mapping_bynumber(hb.type) or {}
    mode = None
    baochip_seen = False
    print("\nWatching (Ctrl-C to stop). Power the Baochip with the test firmware now.\n")
    while True:
        msg = m.recv_match(type=["HEARTBEAT", "STATUSTEXT"], blocking=True, timeout=1)
        if msg is None:
            continue
        if msg.get_type() == "STATUSTEXT":
            if msg.get_srcSystem() == m.target_system:
                print(f"[Cube says] {msg.text}")
            continue
        src = msg.get_srcSystem()
        if src == BAOCHIP_SYSID and not baochip_seen:
            baochip_seen = True
            print("[OK] Baochip heartbeat arrived through TELEM1: Baochip TX -> Cube RX works.")
        elif src == m.target_system and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            name = modes.get(msg.custom_mode, str(msg.custom_mode))
            if name != mode:
                print(f"[Cube mode] {name}")
                mode = name


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

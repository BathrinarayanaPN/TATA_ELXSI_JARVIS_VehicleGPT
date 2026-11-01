"""CARLA -> VehicleGPT Bridge with Real-Time Heads-Up Display (HUD) & Remote Control.

Features:
  1. Real-time CAN bus telemetry + vehicle controls drawn directly in CARLA 3D world space
     (floating above the vehicle in the CarlaUE4 viewport via world.debug.draw_string).
  2. Onboard RGB camera sensor with futuristic automotive HUD overlay (speedometer,
     SOC gradient bar, battery thermal monitor, 4-wheel TPMS, throttle/brake/steer bars,
     ADAS status, DTC diagnostics, and VehicleGPT AI alerts).
  3. Built-in HTTP MJPEG video stream (http://localhost:50050/stream) for live Web UI integration.
  4. Remote vehicle control via UDP/HTTP (autopilot toggle, manual steer/throttle/brake,
     weather presets, camera switching, fault injection).
  5. Optional interactive desktop Pygame window (--hud or --pygame) with keyboard driving.
  6. Closed-loop safety: Battery thermal derate from fault injection actively slows CARLA car.
"""
import argparse
import json
import math
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

import config
from sim.carla_hud import CarlaHUDEngine
from sim.live_simulator import VehicleNode

# Weather Presets
WEATHER_PRESETS = {
    "ClearNoon": "ClearNoon",
    "CloudyNoon": "CloudyNoon",
    "WetSunset": "WetSunset",
    "MidRainyNoon": "MidRainyNoon",
    "HardRainNoon": "HardRainNoon",
    "SoftRainSunset": "SoftRainSunset",
}

# Global shared frame for HTTP MJPEG streaming
_latest_jpeg_frame = None
_frame_lock = threading.Lock()


class MJPEGStreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress noisy streaming access logs

    def do_GET(self):
        global _latest_jpeg_frame, _frame_lock
        if self.path in ("/stream", "/video_feed", "/"):
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    with _frame_lock:
                        frame = _latest_jpeg_frame
                    if frame is not None:
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(frame)))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.04)  # ~25 FPS
            except (ConnectionResetError, BrokenPipeError):
                pass
        elif self.path in ("/snapshot", "/snapshot.jpg"):
            with _frame_lock:
                frame = _latest_jpeg_frame
            if frame is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
            else:
                self.send_response(503)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def start_mjpeg_server(port=50050):
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), MJPEGStreamHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[carla] Live HUD camera MJPEG stream available at http://localhost:{port}/stream")
        return server
    except Exception as e:
        print(f"[carla] could not start MJPEG server on port {port}: {e}")
        return None


def configure_traffic_manager(tm, vehicle):
    """Tunes CARLA Traffic Manager for smooth urban and highway driving.
    Prevents red-light/stop-sign deadlocks in towns and eliminates curb-swerving."""
    try:
        tm.set_synchronous_mode(True)
        # 1. Ignore traffic lights to prevent deadlocks at broken town intersections
        tm.ignore_lights_percentage(vehicle, 100)
        # 2. Ignore stop signs to prevent permanent halting at empty town junctions
        tm.ignore_signs_percentage(vehicle, 100)
        # 3. Disable random auto lane changing so vehicle doesn't steer into curbs/sidewalks in town
        tm.auto_lane_change(vehicle, False)
        tm.random_left_lanechange_percentage(vehicle, 0)
        tm.random_right_lanechange_percentage(vehicle, 0)
        # 4. Safe distance to leading vehicle
        tm.distance_to_leading_vehicle(vehicle, 2.5)
        # 5. Drive briskly: -25% gives ~38-42 km/h in 30 km/h city streets and ~105 km/h on highway
        tm.vehicle_percentage_speed_difference(vehicle, -25)
    except Exception as e:
        print(f"[carla] TM configuration warning: {e}")


def get_best_spawn_point(world, town_name="Town04", town_preference="highway"):
    """Picks optimal spawn point for Town04 (highway vs town center) and urban maps."""
    sps = world.get_map().get_spawn_points()
    if not sps:
        return None
    m_name = world.get_map().name
    if "Town04" in m_name or "Town04" in town_name:
        if town_preference == "city" or "city" in town_name.lower():
            # SP 139 is on Road 6 right in the heart of Town04's town center
            return sps[139] if len(sps) > 139 else sps[0]
        else:
            # SP 0 is on Road 43 highway loop
            return sps[0]
    return sps[0]


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import carla

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--town", default="Town04", help="Town04 has a highway loop + city section")
    ap.add_argument("--vehicle", default="vehicle.tesla.model3")
    ap.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for CARLA")
    ap.add_argument("--stream-port", type=int, default=50050, help="HTTP MJPEG stream port")
    ap.add_argument("--hud", "--pygame", action="store_true", help="Open an interactive Pygame desktop HUD window")
    args = ap.parse_args()

    try:
        node = VehicleNode(use_driver=False)
    except OSError as e:
        raise SystemExit(
            f"[carla] cannot start: {e}\n"
            "Another VehicleGPT simulator/bridge is still running and holds the control port (UDP 50020).\n"
            "Close old windows or run: netstat -ano | findstr 50020  then  taskkill /PID <pid> /F")
    node.road_pin = None

    print(f"[carla] connecting to {args.host}:{args.port} (timeout {args.timeout:.0f}s)...")
    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    world = client.get_world()
    print(f"[carla] connected, server {client.get_server_version()}, current map {world.get_map().name}")

    if args.town and not world.get_map().name.endswith(args.town):
        print(f"[carla] loading {args.town} - map loading in progress...")
        world = client.load_world(args.town)
    print(f"[carla] map ready: {world.get_map().name}")

    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05  # 20 Hz tick
    world.apply_settings(settings)
    tm = client.get_trafficmanager(8000)
    tm.set_synchronous_mode(True)

    bp_lib = world.get_blueprint_library()
    bp = (bp_lib.filter(args.vehicle) or bp_lib.filter("vehicle.*"))[0]
    best_sp = get_best_spawn_point(world, args.town)
    vehicle = world.try_spawn_actor(bp, best_sp) if best_sp else None
    if vehicle is None:
        for sp in world.get_map().get_spawn_points():
            vehicle = world.try_spawn_actor(bp, sp)
            if vehicle:
                break
    if vehicle is None:
        settings.synchronous_mode = False
        world.apply_settings(settings)
        raise SystemExit("[carla] could not spawn ego vehicle. Check CARLA spawn points.")

    autopilot_enabled = True
    vehicle.set_autopilot(autopilot_enabled, tm.get_port())
    configure_traffic_manager(tm, vehicle)

    # Sensors
    lane_hits, crashes = [], []
    lane = world.spawn_actor(bp_lib.find("sensor.other.lane_invasion"), carla.Transform(), attach_to=vehicle)
    lane.listen(lambda e: lane_hits.append(e))
    col = world.spawn_actor(bp_lib.find("sensor.other.collision"), carla.Transform(), attach_to=vehicle)
    col.listen(lambda e: crashes.append(e))

    # RGB Camera Sensor for HUD and Live Video Stream
    cam_bp = bp_lib.find("sensor.camera.rgb")
    cam_bp.set_attribute("image_size_x", "1280")
    cam_bp.set_attribute("image_size_y", "720")
    cam_bp.set_attribute("fov", "90")

    cam_positions = {
        "cockpit": carla.Transform(carla.Location(x=1.1, y=-0.35, z=1.25), carla.Rotation(pitch=-3)),
        "chase": carla.Transform(carla.Location(x=-5.5, z=2.4), carla.Rotation(pitch=-12)),
        "hood": carla.Transform(carla.Location(x=2.0, z=1.0), carla.Rotation(pitch=-5)),
    }
    current_cam_view = "cockpit"
    camera = world.spawn_actor(cam_bp, cam_positions[current_cam_view], attach_to=vehicle)

    latest_raw_frame = None
    cam_lock = threading.Lock()

    def camera_callback(image):
        nonlocal latest_raw_frame
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))
        bgr = array[:, :, :3].copy()
        with cam_lock:
            latest_raw_frame = bgr

    camera.listen(camera_callback)

    hud = CarlaHUDEngine(1280, 720)
    spectator = world.get_spectator()
    start_mjpeg_server(args.stream_port)

    # Optional Pygame display initialization
    pg_screen, pg_clock = None, None
    if args.hud:
        try:
            import pygame
            pygame.init()
            pygame.font.init()
            pg_screen = pygame.display.set_mode((1280, 720))
            pygame.display.set_caption("VehicleGPT CARLA Cockpit HUD · Tata Elxsi SDV")
            pg_clock = pygame.time.Clock()
            print("[carla] Pygame interactive cockpit display initialized.")
        except Exception as e:
            print(f"[carla] Pygame init failed ({e}), continuing headless stream.")

    manual_control = carla.VehicleControl()
    manual_control.throttle = 0.0
    manual_control.steer = 0.0
    manual_control.brake = 0.0
    manual_control.reverse = False
    manual_control.hand_brake = False

    print("[carla] Ego vehicle spawned, streaming CAN telemetry + HUD. Ctrl+C to stop.")

    last_print = 0
    next_tick = time.time()
    stuck_ticks = 0
    emergency_stop_active = False
    last_node_s = {
        "speed_kmh": 0.0, "soc_pct": 88.0, "batt_temp_c": 29.0, "power_kw": 0.0,
        "coolant_pump_ok": 1, "power_derate": 0, "tire_psi_fl": 36.0, "est_range_km": 340.0
    }

    try:
        while True:
            # 1. Check for incoming control packets on UDP port 50020
            while True:
                try:
                    pkt, _ = node.ctrl.recvfrom(2048)
                    cmd_data = json.loads(pkt.decode())
                    cmd = cmd_data.get("cmd")
                    if cmd == "carla_autopilot":
                        autopilot_enabled = bool(cmd_data.get("value", True))
                        vehicle.set_autopilot(autopilot_enabled, tm.get_port())
                        if autopilot_enabled:
                            configure_traffic_manager(tm, vehicle)
                        node.log.write("vgpt_carla.10", 5, f"Autopilot set to {autopilot_enabled}")
                    elif cmd == "carla_control":
                        autopilot_enabled = False
                        vehicle.set_autopilot(False)
                        manual_control.throttle = float(cmd_data.get("throttle", 0.0))
                        manual_control.steer = float(cmd_data.get("steer", 0.0))
                        manual_control.brake = float(cmd_data.get("brake", 0.0))
                        manual_control.reverse = bool(cmd_data.get("reverse", False))
                        manual_control.hand_brake = bool(cmd_data.get("hand_brake", False))
                        vehicle.apply_control(manual_control)
                    elif cmd == "carla_weather":
                        w_name = cmd_data.get("preset", "ClearNoon")
                        w_attr = getattr(carla.WeatherParameters, w_name, None)
                        if w_attr:
                            world.set_weather(w_attr)
                            node.log.write("vgpt_carla.10", 5, f"CARLA Weather changed to {w_name}")
                    elif cmd == "carla_camera":
                        c_view = cmd_data.get("view", "chase")
                        if c_view in cam_positions and c_view != current_cam_view:
                            current_cam_view = c_view
                            camera.set_transform(cam_positions[current_cam_view])
                    elif cmd in ("carla_town", "carla_world"):
                        raw_town = cmd_data.get("town") or cmd_data.get("value") or "Town04"
                        town_pref = "city" if "city" in raw_town.lower() else "highway"
                        new_town = "Town04" if "Town04" in raw_town else raw_town

                        # If already in Town04 and switching between Highway Loop and Town Center
                        if "Town04" in new_town and world.get_map().name.endswith("Town04"):
                            sp = get_best_spawn_point(world, new_town, town_preference=town_pref)
                            if sp and vehicle:
                                vehicle.set_transform(sp)
                                vehicle.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
                                vehicle.set_autopilot(autopilot_enabled, tm.get_port())
                                configure_traffic_manager(tm, vehicle)
                                stuck_ticks = 0
                                node.road = town_pref
                                node.log.write("vgpt_carla.10", 5, f"Repositioned in Town04 to {town_pref}")
                                print(f"[carla] Repositioned vehicle to Town04 {town_pref} location.")
                        elif not world.get_map().name.endswith(new_town):
                            print(f"[carla] loading requested city map: {new_town}...")
                            node.log.write("vgpt_carla.10", 5, f"Switching CARLA city map to {new_town}")
                            for a in (camera, lane, col, vehicle):
                                try:
                                    a.destroy()
                                except Exception:
                                    pass
                            world = client.load_world(new_town)
                            settings = world.get_settings()
                            settings.synchronous_mode = True
                            settings.fixed_delta_seconds = 0.05
                            world.apply_settings(settings)
                            tm = client.get_trafficmanager(8000)
                            tm.set_synchronous_mode(True)
                            bp_lib = world.get_blueprint_library()
                            bp = (bp_lib.filter(args.vehicle) or bp_lib.filter("vehicle.*"))[0]
                            sp = get_best_spawn_point(world, new_town, town_preference=town_pref)
                            vehicle = world.try_spawn_actor(bp, sp) if sp else None
                            if vehicle is None:
                                for fallback_sp in world.get_map().get_spawn_points():
                                    vehicle = world.try_spawn_actor(bp, fallback_sp)
                                    if vehicle:
                                        break
                            if vehicle:
                                vehicle.set_autopilot(autopilot_enabled, tm.get_port())
                                configure_traffic_manager(tm, vehicle)
                                lane = world.spawn_actor(bp_lib.find("sensor.other.lane_invasion"), carla.Transform(), attach_to=vehicle)
                                lane.listen(lambda e: lane_hits.append(e))
                                col = world.spawn_actor(bp_lib.find("sensor.other.collision"), carla.Transform(), attach_to=vehicle)
                                col.listen(lambda e: crashes.append(e))
                                camera = world.spawn_actor(cam_bp, cam_positions[current_cam_view], attach_to=vehicle)
                                camera.listen(camera_callback)
                                spectator = world.get_spectator()
                                stuck_ticks = 0
                                print(f"[carla] city map {new_town} ready and vehicle respawned.")
                    else:
                        # Forward other fault/scenario commands to VehicleNode
                        node.apply(cmd_data)
                except (BlockingIOError, OSError):
                    break
                except Exception as e:
                    node.log.write("vgpt_carla.10", 3, f"Control error: {e}")

            # 2. Handle Pygame keyboard input if display is active
            if pg_screen:
                import pygame
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        break
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_p:
                            autopilot_enabled = not autopilot_enabled
                            vehicle.set_autopilot(autopilot_enabled, tm.get_port())
                        elif event.key == pygame.K_c:
                            views = list(cam_positions.keys())
                            next_idx = (views.index(current_cam_view) + 1) % len(views)
                            current_cam_view = views[next_idx]
                            camera.set_transform(cam_positions[current_cam_view])
                        elif event.key == pygame.K_SPACE:
                            manual_control.hand_brake = not manual_control.hand_brake

                if not autopilot_enabled:
                    keys = pygame.key.get_pressed()
                    manual_control.throttle = 0.7 if (keys[pygame.K_UP] or keys[pygame.K_w]) else 0.0
                    manual_control.brake = 0.8 if (keys[pygame.K_DOWN] or keys[pygame.K_s]) else 0.0
                    manual_control.steer = -0.5 if (keys[pygame.K_LEFT] or keys[pygame.K_a]) else (
                        0.5 if (keys[pygame.K_RIGHT] or keys[pygame.K_d]) else 0.0)
                    manual_control.reverse = bool(keys[pygame.K_r])
                    vehicle.apply_control(manual_control)

            # 3. Step CARLA simulation world
            world.tick()
            try:
                if vehicle is None or not vehicle.is_alive:
                    continue
                v = vehicle.get_velocity()
                speed = 3.6 * math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
                speed_lim = vehicle.get_speed_limit()
            except Exception:
                continue

            if node.road_pin is None:
                current_map = world.get_map().name
                if "Town04" in current_map:
                    loc = vehicle.get_transform().location
                    wp = world.get_map().get_waypoint(loc)
                    is_hwy = (speed_lim >= 70) or (wp and wp.road_id in (35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47))
                    node.road = "highway" if is_hwy else "city"
                elif "Town05" in current_map:
                    node.road = "highway" if speed_lim >= 70 else "city"
                else:
                    node.road = "city"
                node.road_until = time.time() + 3600
            if lane_hits and node.adas:
                node.pending_disengage = "LANE_MARKINGS_LOST"
            if crashes:
                node.log.write("vgpt_carla.77", 1, f"Collision detected with {crashes[-1].other_actor.type_id}")
                if node.adas:
                    node.pending_disengage = "DRIVER_BRAKE_OVERRIDE"
            lane_hits.clear()
            crashes.clear()

            # 4. Step vehicle physical node and publish CAN / SOME-IP
            s = node.tick(0.05, speed_override=(0.0 if emergency_stop_active else speed))
            last_node_s = s

            # Calculate active fault count
            active_fault_count = (
                (1 if getattr(node.ev.faults, "coolant_pump_failed", False) else 0)
                + (1 if getattr(node, "tire_leak", False) else 0)
                + (1 if getattr(node, "inverter_fault", False) else 0)
                + (1 if getattr(node, "adas_fault", False) else 0)
                + (1 if getattr(node, "heatwave_fault", False) else 0)
            )

            # CARLA ego vehicle STOPS ONLY when ALL faults are triggered simultaneously
            # (or when the dedicated "Trigger All Faults" / all_faults command is active).
            # For a single fault, the vehicle does NOT stop; it continues cruising with derate/warnings!
            all_faults_active = (
                getattr(node, "all_faults_active", False)
                or (getattr(node, "fault_stop_override", False) and active_fault_count >= 5)
                or (active_fault_count >= 5)
            )

            # Emergency Safe Stop Controller:
            # Active ONLY when ALL faults are triggered simultaneously
            if all_faults_active:
                if not emergency_stop_active:
                    emergency_stop_active = True
                    print("\n[carla] [SAFETY HALT] ALL FAULTS ACTIVE -> TOTAL SYSTEM EMERGENCY SAFE STOP (Vehicle Speed -> 0.0 km/h)!")
                    node.log.write("vgpt_safety.01", 1, "ALL FAULTS ACTIVE: Minimum Risk Condition Full Halt in Simulation")

                vehicle.set_autopilot(False)
                stop_ctrl = carla.VehicleControl(throttle=0.0, steer=0.0, brake=1.0, hand_brake=True)
                vehicle.apply_control(stop_ctrl)
                curr_control = stop_ctrl
                stuck_ticks = 0

                # Decelerate residual momentum cleanly to 0 km/h
                if speed > 0.2:
                    v = vehicle.get_velocity()
                    vehicle.set_target_velocity(carla.Vector3D(v.x * 0.2, v.y * 0.2, v.z * 0.2))
                else:
                    vehicle.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            else:
                if emergency_stop_active:
                    emergency_stop_active = False
                    print("\n[carla] [RESUMED] NOMINAL DRIVE RESTORED -> RELEASING BRAKES & RESUMING DRIVE!")
                    node.log.write("vgpt_safety.01", 4, "EMERGENCY SAFE STOP CLEARED: Resuming drive")
                    clear_ctrl = carla.VehicleControl(throttle=0.0, steer=0.0, brake=0.0, hand_brake=False)
                    vehicle.apply_control(clear_ctrl)
                    if autopilot_enabled:
                        vehicle.set_autopilot(True, tm.get_port())
                        configure_traffic_manager(tm, vehicle)
                    stuck_ticks = 0

                # Town Watchdog & Auto-Unstuck Controller (only active when not stopped for faults)
                if autopilot_enabled:
                    if speed < 1.2:
                        stuck_ticks += 1
                    else:
                        stuck_ticks = 0

                    if stuck_ticks == 45:  # Stuck for ~2.2 seconds at a curb or corner
                        vehicle.set_autopilot(False)
                        rev_ctrl = carla.VehicleControl(throttle=0.65, steer=-0.45, reverse=True)
                        vehicle.apply_control(rev_ctrl)
                    elif stuck_ticks == 60:  # Reversed for 0.75 seconds to clear curb
                        vehicle.set_autopilot(True, tm.get_port())
                        configure_traffic_manager(tm, vehicle)
                    elif stuck_ticks >= 100:  # Wedged: realign with driving lane center
                        cur_loc = vehicle.get_transform().location
                        wp = world.get_map().get_waypoint(cur_loc, project_to_road=True, lane_type=carla.LaneType.Driving)
                        if wp:
                            next_wps = wp.next(7.0)
                            target_wp = next_wps[0] if next_wps else wp
                            target_trans = carla.Transform(
                                target_wp.transform.location + carla.Location(z=0.35),
                                target_wp.transform.rotation
                            )
                            vehicle.set_transform(target_trans)
                            fwd = target_wp.transform.get_forward_vector()
                            vehicle.set_target_velocity(fwd * 7.5)
                        vehicle.set_autopilot(True, tm.get_port())
                        configure_traffic_manager(tm, vehicle)
                        stuck_ticks = 0

                try:
                    if autopilot_enabled:
                        # For single fault (like coolant pump derate), cruise at a safe derated speed (~20 km/h) without stopping!
                        tm.vehicle_percentage_speed_difference(vehicle, 40 if s.get("power_derate") else -25)
                    else:
                        vehicle.apply_control(manual_control)
                    curr_control = vehicle.get_control()
                except Exception:
                    curr_control = manual_control

            try:
                t = vehicle.get_transform()
                fwd = t.get_forward_vector()
                spectator.set_transform(carla.Transform(
                    t.location - fwd * 9 + carla.Location(z=4.5), carla.Rotation(pitch=-14, yaw=t.rotation.yaw)))
            except Exception:
                pass

            # 5. Draw 3D in-CARLA world HUD (floating above car in CarlaUE4 window)
            hud.draw_3d_world_hud(world, t, s, curr_control, autopilot_enabled, node.adas, road=node.road)

            # 6. Process RGB camera frame and apply Futuristic HUD Overlay
            with cam_lock:
                frame_to_render = latest_raw_frame.copy() if latest_raw_frame is not None else None

            if frame_to_render is not None:
                hud_frame = hud.render_overlay(
                    frame_to_render,
                    telemetry=s,
                    control=curr_control,
                    autopilot=autopilot_enabled,
                    adas=node.adas,
                    road=node.road,
                    town=world.get_map().name.split("/")[-1]
                )

                # Compress to JPEG for Web MJPEG stream
                ret, jpeg = cv2.imencode(".jpg", hud_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if ret:
                    global _latest_jpeg_frame, _frame_lock
                    with _frame_lock:
                        _latest_jpeg_frame = jpeg.tobytes()

                # Render to Pygame window if active
                if pg_screen:
                    import pygame
                    # Convert BGR to RGB for pygame
                    rgb_frame = cv2.cvtColor(hud_frame, cv2.COLOR_BGR2RGB)
                    pg_surface = pygame.surfarray.make_surface(rgb_frame.swapaxes(0, 1))
                    pg_screen.blit(pg_surface, (0, 0))
                    pygame.display.flip()
                    pg_clock.tick(30)

            # Console status print every 0.5 s for live terminal monitoring
            if time.time() - last_print > 0.5:
                last_print = time.time()
                gear_str = "R" if curr_control.reverse else ("P" if curr_control.hand_brake else f"D")
                derate_str = "ACTIVE (-50%)" if s.get('power_derate') else "OFF"
                pump_str = "OK" if s.get('coolant_pump_ok', 1) else "FAILED (P0C73)"
                print(f"\r[TATA ELXSI SDV] {node.road.upper():<7} | SPEED: {speed:5.1f} km/h | GEAR: {gear_str} | "
                      f"SOC: {s['soc_pct']:4.1f}% | TEMP: {s['batt_temp_c']:4.1f}C | PWR: {s['power_kw']:+5.1f}kW | "
                      f"PUMP: {pump_str:<14} | DERATE: {derate_str:<12} | "
                      f"AP: {'ON' if autopilot_enabled else 'OFF'} | ADAS: {'ON' if node.adas else 'OFF'}", end="", flush=True)

            next_tick += 0.05
            delay = next_tick - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.time()

    except KeyboardInterrupt:
        pass
    finally:
        print("[carla] cleaning up CARLA actors and settings...")
        try:
            settings.synchronous_mode = False
            world.apply_settings(settings)
            tm.set_synchronous_mode(False)
        except Exception:
            pass
        for a in (camera, lane, col, vehicle):
            try:
                a.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
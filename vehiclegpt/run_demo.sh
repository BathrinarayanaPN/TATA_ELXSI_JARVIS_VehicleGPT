#!/usr/bin/env bash
# VehicleGPT demo for Linux / Raspberry Pi / Docker.   ./run_demo.sh [carla]
set -e
cd "$(dirname "$0")"
[ -f data/vehicle.db ] || python -m sim.seed_history
python -m collector.collector & COL=$!
sleep 3
if [ "$1" = "carla" ]; then python -m sim.carla_bridge --town Town04 & SIM=$!
else python -m sim.live_simulator & SIM=$!; fi
trap 'kill $COL $SIM 2>/dev/null' EXIT
python -m streamlit run ui/app.py --server.address 0.0.0.0 --server.headless true

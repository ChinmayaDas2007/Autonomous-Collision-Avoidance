#!/usr/bin/env bash
# Project Kessler — Ground Operations Automation Pipeline
# This script automates dependency installation, unit testing, and executes a full prediction pipeline cycle.

set -e

echo "=========================================================="
echo "    Project Kessler — Ground Operations Pipeline          "
echo "=========================================================="

# 1. Ensure working directory is the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 2. Setup Virtual Environment
echo "[*] Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "    Created new virtual environment 'venv'."
fi

echo "[*] Activating virtual environment..."
source venv/bin/activate

# 3. Install Dependencies
echo "[*] Installing required dependencies (SGP4, Skyfield, XGBoost, Requests, Numpy)..."
# We upgrade pip quietly, then install dependencies
pip install -q --upgrade pip
pip install -q sgp4 skyfield xgboost requests numpy

# 4. Run Unit Tests
echo "[*] Running Ground AI Node Unit Tests..."
export PYTHONPATH="$PROJECT_ROOT"
python3 -m unittest gnd_ops/test_ground_ai_node.py
echo "    Unit tests passed successfully!"

# 5. Execute Ground AI Node Single Pipeline Run
echo "[*] Executing Ground AI Node (Phase 1-4 Pipeline)..."
cd gnd_ops
# We run with --once to execute a single prediction cycle.
# We also include --simulate-eclipse to demonstrate the abort pathway.
python3 ground_ai_node.py --once --simulate-eclipse

echo "=========================================================="
echo "    Ground Ops Pipeline Execution Completed Successfully! "
echo "=========================================================="

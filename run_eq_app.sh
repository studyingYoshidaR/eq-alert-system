#!/bin/bash
# Diagnostic launcher. Paths are resolved from this script's own location,
# so the script works regardless of where the repository is cloned.
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "${SCRIPT_DIR}"
echo "Starting Earthquake Alert System..."
echo "Python: $(which python3)"
echo "Version: $(python3 --version)"
echo "Working Dir: $(pwd)"
echo "---"
python3 "${SCRIPT_DIR}/eq_src/eq_app.py" "$@" 2>&1 | tee "${SCRIPT_DIR}/eq_app_launch.log"

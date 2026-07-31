#!/bin/bash

# Get the directory of the current script (absolute path)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
APP_PATH="${SCRIPT_DIR}/eq_src/eq_app.py"

echo "=================================================="
echo "Raspberry Pi Auto-start & Launcher Setup Script"
echo "=================================================="
echo "Project Path: ${SCRIPT_DIR}"
echo "App Path:     ${APP_PATH}"

# Check if eq_app.py exists
if [ ! -f "${APP_PATH}" ]; then
    echo "[Error] eq_app.py could not be found at: ${APP_PATH}"
    exit 1
fi

# 1. Create autostart directory if it doesn't exist
mkdir -p ~/.config/autostart

# 2. Generate autostart desktop entry
AUTOSTART_FILE="$HOME/.config/autostart/eq_app.desktop"
echo "Generating autostart file at: ${AUTOSTART_FILE}"

cat <<EOT > "${AUTOSTART_FILE}"
[Desktop Entry]
Type=Application
Name=Earthquake Alert System
Exec=python3 ${APP_PATH} --show
Path=${SCRIPT_DIR}
StartupNotify=false
Terminal=false
EOT

# 3. Generate Desktop shortcuts
DESKTOP_DIR="$HOME/Desktop"
# Raspberry Pi OS (日本語) ではデスクトップフォルダが「デスクトップ」の場合がある
if [ ! -d "${DESKTOP_DIR}" ]; then
    DESKTOP_DIR="$HOME/デスクトップ"
fi
if [ -d "${DESKTOP_DIR}" ]; then
    TOGGLE_LAUNCHER="${DESKTOP_DIR}/Toggle_Alert_App.desktop"
    QUIT_LAUNCHER="${DESKTOP_DIR}/Quit_Alert_App.desktop"
    
    echo "Generating Desktop Toggle Launcher at: ${TOGGLE_LAUNCHER}"
    cat <<EOT > "${TOGGLE_LAUNCHER}"
[Desktop Entry]
Version=1.0
Type=Application
Name=地震アラート切替
Comment=地震アラート画面の表示／非表示を切り替えます
Exec=python3 ${APP_PATH}
Path=${SCRIPT_DIR}
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
EOT

    echo "Generating Desktop Quit Launcher at: ${QUIT_LAUNCHER}"
    cat <<EOT > "${QUIT_LAUNCHER}"
[Desktop Entry]
Version=1.0
Type=Application
Name=地震アラート終了
Comment=地震アラートシステムを完全に終了します
Exec=python3 ${APP_PATH} --stop
Path=${SCRIPT_DIR}
Icon=application-exit
Terminal=false
Categories=Utility;
EOT

    # Make desktop entries executable
    chmod +x "${TOGGLE_LAUNCHER}" "${QUIT_LAUNCHER}"

    # Mark .desktop files as trusted (required by PCManFM on Raspberry Pi OS)
    if command -v gio &> /dev/null; then
        gio set "${TOGGLE_LAUNCHER}" metadata::trusted true 2>/dev/null
        gio set "${QUIT_LAUNCHER}" metadata::trusted true 2>/dev/null
        echo "Desktop files marked as trusted via gio."
    fi

    echo "Desktop launchers created and made executable."
else
    echo "[Warning] Desktop directory not found. Skipped creating desktop launchers."
fi

# 4. Generate a diagnostic launch script for troubleshooting
DIAG_SCRIPT="${SCRIPT_DIR}/run_eq_app.sh"
echo "Generating diagnostic launch script at: ${DIAG_SCRIPT}"
# 生成物はgit追跡下のため、環境固有の絶対パスを埋め込まない
# (スクリプト自身の位置から解決させる)
cat <<'EOT' > "${DIAG_SCRIPT}"
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
EOT
chmod +x "${DIAG_SCRIPT}"

echo "--------------------------------------------------"
echo "Setup completed successfully!"
echo ""
echo "使い方:"
echo "  1. OS再起動時にアプリは自動起動します。"
echo "  2. デスクトップの「地震アラート切替」でウィンドウのON/OFF。"
echo "  3. デスクトップの「地震アラート終了」でシステムを完全停止。"
echo ""
echo "トラブルシューティング:"
echo "  ショートカットが動かない場合は、ターミナルから以下を実行してください:"
echo "  cd ${SCRIPT_DIR} && ./run_eq_app.sh"
echo "  エラーログは ${SCRIPT_DIR}/eq_app_launch.log に保存されます。"
echo "=================================================="

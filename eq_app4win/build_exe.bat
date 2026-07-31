@echo off
cd /d "%~dp0"
echo ============================================
echo  EqAlertSystem -- PyInstaller Build
echo ============================================

:: 依存関係チェック
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller が見つかりません。インストールします...
    pip install pyinstaller
)

:: ビルド実行 (dist\EqAlertSystem\ に出力)
pyinstaller eq_app.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] ビルドに失敗しました。
    pause
    exit /b 1
)

echo.
echo [OK] ビルド完了: dist\EqAlertSystem\EqAlertSystem.exe
echo      フォルダごと配布してください。
echo.
pause

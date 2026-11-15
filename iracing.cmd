@echo off
rem iRacing の telemetry フォルダの .ibt を取り込み、web view をブラウザで開く（Windows）。
rem 要るもの：uv と Node.js。取り込みは別のウィンドウで見張り続け、新しい .ibt ができると書き出し直す。
rem ブラウザを再読み込みすると新しい周が出る。詳しくは README の「iRacing の周を見る」。
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] iRacing の .ibt を取り込んでいます...
uv run python -m ingest.iracing_dir || goto :fail

echo [2/3] 取り込みの見張りを別のウィンドウで始めます
start "lapdiff iRacing" cmd /k uv run python -m ingest.iracing_dir --watch

echo [3/3] web view を起動してブラウザで開きます
cd web
if not exist node_modules (call npm ci || goto :fail)
call npm run dev -- --open
exit /b 0

:fail
echo 失敗しました。上の表示を確かめてください
pause
exit /b 1

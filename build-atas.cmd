@echo off
REM Build (and install) the ATAS History Exporter.
REM Run this from anywhere, e.g.:  C:\Users\look\ss\build-atas.cmd
setlocal
cd /d "%~dp0"
echo === Building ATAS History Exporter ===
dotnet build "atas\HistoryExporter\HistoryExporter.csproj" -c Release -p:Install=true
if errorlevel 1 (
  echo.
  echo Build failed - copy the messages above.
) else (
  echo.
  echo Done. In ATAS add the indicator "History Exporter" to a 1-minute MNQ chart.
)
endlocal
pause

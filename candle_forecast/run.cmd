@echo off
rem ============================================================================
rem  candle_forecast, livello 0: tutto in fila.
rem    1. ambiente conda "candle_forecast" (lo crea se manca, installa le librerie)
rem    2. test (se falliscono si ferma: niente training su codice rotto)
rem    3. prepare: scompatta stores\EUR_USD.hd5.zip, report, campioni
rem    4. run: baseline, LightGBM, GRU su tutti i sistemi del config
rem    5. commit e push dei risultati (modelli, summary, report), se lo chiedi
rem  Si lancia con doppio clic o da "Anaconda Prompt":  run.cmd [config]
rem  Senza argomenti usa config\systems.toml (EURUSD M5); run_h4.cmd passa config\h4.toml.
rem  Con NOPAUSE definita non si ferma alla fine: la pausa la fa chi lo chiama.
rem  Prima volta: circa 5 minuti di installazione. Il training: ore.
rem ============================================================================
setlocal
cd /d "%~dp0"
set "ENV=candle_forecast"
set "CFG="
set "WHAT=EURUSD M5"
if not "%~1"=="" (
    set "CFG=--config %~1"
    set "WHAT=%~n1"
)

rem --- conda: dal PATH o dalle installazioni standard -------------------------
where conda >nul 2>nul
if errorlevel 1 call :find_conda
where conda >nul 2>nul
if errorlevel 1 (
    echo ERRORE: conda non trovato. Installa Miniconda da https://docs.conda.io/en/latest/miniconda.html
    echo oppure lancia run.cmd da "Anaconda Prompt".
    goto :fail
)

rem --- la domanda si fa subito, cosi il training puo girare da solo ------------
choice /c SN /m "A fine training faccio commit e push dei risultati su GitHub"
if errorlevel 2 (set "PUSH=N") else (set "PUSH=S")

echo.
echo [1/5] ambiente %ENV%
call conda env list | findstr /r /c:"^%ENV% " >nul
if errorlevel 1 (
    call conda create -y -n %ENV% --override-channels -c conda-forge python=3.12
    if errorlevel 1 goto :fail
)
call conda activate %ENV%
if errorlevel 1 goto :fail
python -m pip install -q --disable-pip-version-check pandas numpy pytest lightgbm tables
if errorlevel 1 goto :fail
python -m pip install -q --disable-pip-version-check torch --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :fail

echo.
echo [2/5] test (circa 3 minuti)
python -m pytest -q
if errorlevel 1 goto :fail

echo.
echo [3/5] prepare
python -m candle_forecast.cli %CFG% prepare
if errorlevel 1 goto :fail

echo.
echo [4/5] training su tutti i sistemi (lungo)
python -m candle_forecast.cli %CFG% run
if errorlevel 1 goto :fail

echo.
if "%PUSH%"=="N" (
    echo [5/5] push saltato, come chiesto
    goto :done
)
echo [5/5] commit e push dei risultati
git add results
if errorlevel 1 goto :fail
git diff --cached --quiet
if not errorlevel 1 (
    echo niente di nuovo da committare
    goto :done
)
git commit -m "Training livello 0 %WHAT% (%COMPUTERNAME%)"
if errorlevel 1 goto :fail
rem se intanto qualcuno ha spinto sul branch, prima si mette in pari
git pull --rebase --autostash
if errorlevel 1 goto :fail
git push
if errorlevel 1 goto :fail

:done
echo.
echo FATTO. Riepilogo: summary.csv in %CD%\results   Modelli: ^<sistema^>\models\
if not defined NOPAUSE pause
exit /b 0

:fail
echo.
echo ERRORE: il passo qui sopra e' fallito. Copia l'output e mandalo.
if not defined NOPAUSE pause
exit /b 1

:find_conda
for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3") do (
    if exist "%%~D\condabin\conda.bat" (
        set "PATH=%%~D\condabin;%PATH%"
        exit /b 0
    )
)
exit /b 0

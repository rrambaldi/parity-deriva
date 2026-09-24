@echo off
rem ============================================================================
rem  candle_forecast: Kronos addestrato da zero, da confrontare con LightGBM.
rem    1. ambiente conda "candle_forecast" e librerie di Kronos
rem    2. GPU: se c'e' una NVIDIA e torch non la vede, installa torch con CUDA
rem    3. prova veloce (--smoke): se fallisce si ferma prima delle ore di training
rem    4. Kronos: tokenizer, modello, previsioni sul test (results\kronos\...)
rem    5. commit e push di previsioni e log, se lo chiedi (i modelli .pt restano qui)
rem  Si lancia con doppio clic o da "Anaconda Prompt":  run_kronos.cmd
rem  Gli argomenti passano a kronos_scratch, es.:  run_kronos.cmd --pretrained
rem  Con GPU: circa un'ora. Solo CPU: molte ore (lo stima dopo i primi passi).
rem ============================================================================
setlocal
cd /d "%~dp0"
set "ENV=candle_forecast"

where conda >nul 2>nul
if errorlevel 1 call :find_conda
where conda >nul 2>nul
if errorlevel 1 (
    echo ERRORE: conda non trovato. Installa Miniconda da https://docs.conda.io/en/latest/miniconda.html
    echo oppure lancia run_kronos.cmd da "Anaconda Prompt".
    goto :fail
)

choice /c SN /m "A fine training faccio commit e push delle previsioni su GitHub"
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
python -m pip install -q --disable-pip-version-check pandas numpy tables einops huggingface_hub tqdm safetensors
if errorlevel 1 goto :fail
python -c "import torch" 2>nul
if errorlevel 1 (
    python -m pip install -q --disable-pip-version-check torch --index-url https://download.pytorch.org/whl/cpu
    if errorlevel 1 goto :fail
)

echo.
echo [2/5] GPU
python -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
    where nvidia-smi >nul 2>nul
    if errorlevel 1 (
        echo nessuna GPU NVIDIA: si va su CPU, il training da zero durera' molte ore
    ) else (
        echo GPU NVIDIA trovata, torch non la vede: installo torch con CUDA
        python -m pip uninstall -y -q torch
        python -m pip install -q --disable-pip-version-check torch --index-url https://download.pytorch.org/whl/cu126
        if errorlevel 1 (
            echo ATTENZIONE: torch con CUDA non installato, rimetto quello per CPU
            python -m pip install -q --disable-pip-version-check torch --index-url https://download.pytorch.org/whl/cpu
            if errorlevel 1 goto :fail
        )
    )
)
python -c "import torch; print('dispositivo:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"

echo.
echo [3/5] prova veloce
python -m candle_forecast.kronos_scratch --smoke
if errorlevel 1 goto :fail

echo.
echo [4/5] Kronos (lungo)
python -m candle_forecast.kronos_scratch %*
if errorlevel 1 goto :fail

echo.
if "%PUSH%"=="N" (
    echo [5/5] push saltato, come chiesto
    goto :done
)
echo [5/5] commit e push delle previsioni
git add results/kronos
if errorlevel 1 goto :fail
git diff --cached --quiet
if not errorlevel 1 (
    echo niente di nuovo da committare
    goto :done
)
git commit -m "Kronos su EURUSD M5 (%COMPUTERNAME%)"
if errorlevel 1 goto :fail
git pull --rebase --autostash
if errorlevel 1 goto :fail
git push
if errorlevel 1 goto :fail

:done
echo.
echo FATTO. Previsioni in %CD%\results\kronos\   Modelli (.pt, non in git) nella stessa cartella.
pause
exit /b 0

:fail
echo.
echo ERRORE: il passo qui sopra e' fallito. Copia l'output e mandalo.
pause
exit /b 1

:find_conda
for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3") do (
    if exist "%%~D\condabin\conda.bat" (
        set "PATH=%%~D\condabin;%PATH%"
        exit /b 0
    )
)
exit /b 0

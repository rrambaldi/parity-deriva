@echo off
rem ============================================================================
rem  candle_forecast livello 0 su H4 dal 2003, 10 coppie (config\h4.toml).
rem    1. scarica dal server le coppie H4 (data/<COPPIA>_H4.tbz) in stores\, con scp
rem    2. poi fa tutto quello che fa run.cmd: ambiente, test, prepare, training, push
rem  Serve la chiave SSH del PC autorizzata sul server, altrimenti scp chiede la password.
rem  Una coppia nuova: il suo .tbz nella data/ del server e una voce in config\h4.toml.
rem ============================================================================
setlocal
cd /d "%~dp0"
set "SERVER=rrambaldi@78.47.187.30"
set "PORT=3722"
set "REMOTE=/home/rrambaldi/progetti/parity-deriva/data"

echo [0] scarico le coppie H4 da %SERVER%:%REMOTE%
scp -P %PORT% "%SERVER%:%REMOTE%/*_H4.tbz" stores
if errorlevel 1 (
    echo ERRORE: scp non riuscito. Controlla SERVER e PORT qui sopra e la chiave SSH del PC.
    pause
    exit /b 1
)
call "%~dp0run.cmd" config\h4.toml

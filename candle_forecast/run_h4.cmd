@echo off
rem candle_forecast livello 0 su H4 dal 2003, 10 coppie (config\h4.toml). Fa tutto quello che fa
rem run.cmd: ambiente, test, prepare, training, e push dei risultati se lo chiedi.
call "%~dp0run.cmd" config\h4.toml

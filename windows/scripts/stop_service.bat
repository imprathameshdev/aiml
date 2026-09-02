@echo off
powershell -Command "Start-Process -FilePath \"%~dp0bin\nssm.exe\" -ArgumentList 'stop LotVulture' -Verb RunAs -WindowStyle Hidden"
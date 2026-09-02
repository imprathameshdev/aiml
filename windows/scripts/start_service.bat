@echo off
powershell -Command "Start-Process -FilePath \"%~dp0bin\nssm.exe\" -ArgumentList 'start LotVulture' -Verb RunAs -WindowStyle Hidden"
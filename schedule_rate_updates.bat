@echo off
REM Daily Reference Rates Updater for Windows Task Scheduler
REM 
REM To schedule this to run daily:
REM 1. Open Task Scheduler (taskschd.msc)
REM 2. Create Basic Task
REM 3. Trigger: Daily at 6:00 AM (before market open)
REM 4. Action: Start a program
REM 5. Program: C:\Users\jsper\dev\Github\new_finance\bdc_extractor_standalone\schedule_rate_updates.bat
REM 6. Start in: C:\Users\jsper\dev\Github\new_finance\bdc_extractor_standalone

cd /d "%~dp0"
python update_reference_rates.py >> logs\rate_updates.log 2>&1

REM Optional: Restart dev server if running to pick up new rates
REM (You can skip this if you want to wait for next manual refresh)
echo Rate update completed at %date% %time% >> logs\rate_updates.log

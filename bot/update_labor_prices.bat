@echo off
chcp 65001 >nul

cd /d C:\Users\Марк\tg_pipeline\bot

echo ==========================================
echo СБОР ЦЕН РАБОТ PROFI.RU
echo ==========================================
echo.
echo Сейчас откроется браузер.
echo Если Profi попросит SMS - войди руками.
echo После входа вернись в это окно и нажми Enter.
echo.

python collect_labor_market.py --login-wait --sleep 8 --push

echo.
echo ==========================================
echo ГОТОВО
echo Если git push прошел успешно, Render начнет автодеплой.
echo ==========================================
echo.

pause
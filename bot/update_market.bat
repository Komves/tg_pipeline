@echo off
chcp 65001 > nul
cd /d C:\Users\Марк\tg_pipeline\bot

echo ============================================
echo Updating Vesya market snapshot
echo ============================================

python collect_market.py --output analytics_agent/data/market_cache.json

echo.
echo ============================================
echo Done. Press any key to close.
echo ============================================
pause > nul
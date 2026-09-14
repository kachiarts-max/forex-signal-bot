# Forex & Crypto Signal Telegram Bot

Multi-timeframe confluence strategy bot that analyses Forex, Commodities and Crypto every 15 minutes and sends high-quality signals.

## Strategy
- **1H**: Trend filter using EMA 50 & EMA 200
- **15m**: RSI pullback + MACD histogram confirmation
- Only signals when both timeframes agree

## Features
- Choose any combination of pairs (majors, gold, oil, BTC, ETH, etc.)
- Interactive pair selection via buttons
- Force analysis anytime
- Pause / resume signals
- Clean, professional signal format
- Strong risk disclaimer on every signal

## Setup (GitHub Codespaces)

1. Open this repository in Codespaces
2. Create a file named `.env` with:
TELEGRAM_BOT_TOKEN=8970693786:AAEX4w_PlqpSTv8rnFZboF-x7x9fcJm_Z30
ADMIN_ID=6301693854
TWELVE_DATA_KEY=9WX2QEE1HK3VC4I2
3. Install dependencies:
```bash
pip install -r requirements.txt
Run the bot:
python bot.py
Keep Codespace Alive (temporary solution)
Codespaces sleep after inactivity. To keep it running longer you can run a simple keep-alive loop in another terminal:
while true; do echo "alive $(date)"; sleep 300; done
Recommended for production: Deploy to Railway, Render or a VPS instead of Codespaces for true 24/7 uptime.
Commands
/start – Welcome & main menu
/pairs – Manage watched pairs
/status – Bot status
/analyze – Run analysis immediately
/stop – Pause signals
/resume – Resume signals
Disclaimer
This bot is for educational purposes only. It is not financial advice.
Trading forex, commodities and crypto involves substantial risk of loss.
You are solely responsible for your trading decisions.

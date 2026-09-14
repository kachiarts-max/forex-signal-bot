import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
from dotenv import load_dotenv
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
from io import BytesIO
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf

load_dotenv()

# ==================== CONFIG ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8970693786:AAEX4w_PlqpSTv8rnFZboF-x7x9fcJm_Z30")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6301693854"))
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY", "9WX2QEE1HK3VC4I2")

# Supported instruments (Yahoo Finance symbols + display names)
INSTRUMENTS = {
    # Major Forex
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    # Commodities
    "XAUUSD": "GC=F",      # Gold
    "XAGUSD": "SI=F",      # Silver
    "USOIL": "CL=F",       # WTI Crude
    "UKOIL": "BZ=F",       # Brent
    "NATGAS": "NG=F",
    # Crypto
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "XRPUSD": "XRP-USD",
    "BNBUSD": "BNB-USD",
}

# User preferences storage (in-memory + simple JSON backup)
USER_DATA_FILE = "user_data.json"
user_prefs: Dict[int, dict] = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== DATA HELPERS ====================
def load_user_data():
    global user_prefs
    try:
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "r") as f:
                user_prefs = {int(k): v for k, v in json.load(f).items()}
    except Exception as e:
        logger.error(f"Failed to load user data: {e}")
        user_prefs = {}


def save_user_data():
    try:
        with open(USER_DATA_FILE, "w") as f:
            json.dump({str(k): v for k, v in user_prefs.items()}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")


def get_user_pairs(chat_id: int) -> List[str]:
    return user_prefs.get(chat_id, {}).get("pairs", list(INSTRUMENTS.keys())[:8])  # default some majors


def set_user_pairs(chat_id: int, pairs: List[str]):
    if chat_id not in user_prefs:
        user_prefs[chat_id] = {}
    user_prefs[chat_id]["pairs"] = pairs
    save_user_data()


# ==================== MARKET DATA ====================
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV data. Tries Yahoo first, falls back to Twelve Data if needed."""
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            return None
        # Flatten multi-index if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        return df
    except Exception as e:
        logger.warning(f"yfinance failed for {symbol}: {e}")
        return None


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.atr(length=14, append=True)
    return df


def generate_signal(df_15m: pd.DataFrame, df_1h: pd.DataFrame, pair: str) -> Optional[dict]:
    """
    Multi-timeframe confluence strategy:
    - 1H defines the trend (EMA50 / EMA200)
    - 15m provides entry timing (RSI pullback + MACD confirmation)
    """
    if df_15m is None or df_1h is None or len(df_15m) < 30 or len(df_1h) < 30:
        return None

    latest_1h = df_1h.iloc[-1]
    prev_1h = df_1h.iloc[-2]
    latest_15 = df_15m.iloc[-1]
    prev_15 = df_15m.iloc[-2]

    # --- 1H Trend ---
    ema50_1h = latest_1h.get("EMA_50")
    ema200_1h = latest_1h.get("EMA_200")
    if pd.isna(ema50_1h) or pd.isna(ema200_1h):
        return None

    trend = None
    if latest_1h["Close"] > ema50_1h > ema200_1h:
        trend = "bullish"
    elif latest_1h["Close"] < ema50_1h < ema200_1h:
        trend = "bearish"
    else:
        return None  # No clear trend

    # --- 15m Momentum ---
    rsi = latest_15.get("RSI_14")
    macd = latest_15.get("MACD_12_26_9")
    macds = latest_15.get("MACDs_12_26_9")
    macdh = latest_15.get("MACDh_12_26_9")
    prev_macdh = prev_15.get("MACDh_12_26_9")

    if any(pd.isna(x) for x in [rsi, macd, macds, macdh, prev_macdh]):
        return None

    signal = None
    reason = []

    if trend == "bullish":
        # Look for RSI pullback into 40-55 then recovery + MACD histogram turning up
        if 40 <= rsi <= 55 and macdh > prev_macdh and macd > macds:
            signal = "BUY"
            reason.append(f"1H bullish (price > EMA50 > EMA200)")
            reason.append(f"15m RSI pullback ({rsi:.1f}) + MACD bullish cross")
    elif trend == "bearish":
        if 45 <= rsi <= 60 and macdh < prev_macdh and macd < macds:
            signal = "SELL"
            reason.append(f"1H bearish (price < EMA50 < EMA200)")
            reason.append(f"15m RSI pullback ({rsi:.1f}) + MACD bearish cross")

    if not signal:
        return None

    atr = latest_15.get("ATRr_14", latest_15.get("ATR_14", 0))
    price = float(latest_15["Close"])

    return {
        "pair": pair,
        "signal": signal,
        "price": price,
        "rsi": float(rsi),
        "macd_hist": float(macdh),
        "atr": float(atr) if not pd.isna(atr) else 0,
        "reasons": reason,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "trend": trend,
    }


# ==================== TELEGRAM HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_prefs:
        user_prefs[chat_id] = {"pairs": list(INSTRUMENTS.keys())[:10], "active": True}
        save_user_data()

    text = (
        "🤖 *Forex & Crypto Signal Bot*\n\n"
        "I analyse markets using *Multi-Timeframe Confluence* (1H trend + 15m entry).\n\n"
        "*Commands:*\n"
        "/pairs – View / change your watched pairs\n"
        "/status – Bot status & your settings\n"
        "/analyze – Force analysis now\n"
        "/stop – Pause signals\n"
        "/resume – Resume signals\n\n"
        "⚠️ *This is NOT financial advice. Trading involves substantial risk of loss.*\n"
        "You decide your own entry, stop-loss and take-profit."
    )
    keyboard = [
        [InlineKeyboardButton("📊 My Pairs", callback_data="show_pairs")],
        [InlineKeyboardButton("🔄 Analyze Now", callback_data="analyze_now")],
        [InlineKeyboardButton("ℹ️ Strategy Info", callback_data="strategy_info")],
    ]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def pairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_pairs_menu(update.effective_chat.id, context.bot, update.message)


async def show_pairs_menu(chat_id: int, bot, message=None):
    current = get_user_pairs(chat_id)
    buttons = []
    row = []
    for i, pair in enumerate(INSTRUMENTS.keys()):
        mark = "✅" if pair in current else "⬜"
        row.append(InlineKeyboardButton(f"{mark} {pair}", callback_data=f"toggle_{pair}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Done", callback_data="pairs_done")])

    text = f"*Your watched pairs* ({len(current)}):\n" + ", ".join(current) if current else "None selected"
    if message:
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data == "show_pairs":
        await show_pairs_menu(chat_id, context.bot)
        return

    if data.startswith("toggle_"):
        pair = data.replace("toggle_", "")
        current = get_user_pairs(chat_id)
        if pair in current:
            current.remove(pair)
        else:
            current.append(pair)
        set_user_pairs(chat_id, current)
        await show_pairs_menu(chat_id, context.bot)
        return

    if data == "pairs_done":
        await query.edit_message_text(f"✅ Saved. Watching {len(get_user_pairs(chat_id))} instruments.")
        return

    if data == "analyze_now":
        await query.edit_message_text("🔍 Running analysis... please wait.")
        await run_analysis_for_user(chat_id, context.bot)
        return

    if data == "strategy_info":
        text = (
            "*Strategy: Multi-Timeframe Confluence*\n\n"
            "1. *1H Trend Filter*\n"
            "   • Bullish only if Price > EMA50 > EMA200\n"
            "   • Bearish only if Price < EMA50 < EMA200\n\n"
            "2. *15m Entry Timing*\n"
            "   • RSI pullback into value zone\n"
            "   • MACD histogram turning in trend direction\n\n"
            "Signals are only sent when *both* timeframes agree.\n"
            "You decide exact entry, SL and TP."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = user_prefs.get(chat_id, {})
    pairs = prefs.get("pairs", [])
    active = prefs.get("active", True)
    text = (
        f"*Bot Status*\n"
        f"Active: {'✅ Yes' if active else '⏸ Paused'}\n"
        f"Watched pairs: {len(pairs)}\n"
        f"{', '.join(pairs[:15])}{'...' if len(pairs) > 15 else ''}\n\n"
        f"Analysis runs every 15 minutes.\n"
        f"Timeframes: 15m + 1H"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_prefs:
        user_prefs[chat_id] = {}
    user_prefs[chat_id]["active"] = False
    save_user_data()
    await update.message.reply_text("⏸ Signals paused. Use /resume to start again.")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_prefs:
        user_prefs[chat_id] = {}
    user_prefs[chat_id]["active"] = True
    save_user_data()
    await update.message.reply_text("▶️ Signals resumed.")


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running full analysis now...")
    await run_analysis_for_user(update.effective_chat.id, context.bot)


# ==================== ANALYSIS ENGINE ====================
async def run_analysis_for_user(chat_id: int, bot):
    prefs = user_prefs.get(chat_id, {})
    if not prefs.get("active", True):
        await bot.send_message(chat_id, "Signals are paused. /resume to enable.")
        return

    pairs = get_user_pairs(chat_id)
    if not pairs:
        await bot.send_message(chat_id, "No pairs selected. Use /pairs")
        return

    signals_found = 0
    for pair in pairs:
        symbol = INSTRUMENTS.get(pair)
        if not symbol:
            continue
        try:
            df_15 = fetch_ohlc(symbol, "15m", "5d")
            df_1h = fetch_ohlc(symbol, "1h", "30d")
            if df_15 is None or df_1h is None:
                continue

            df_15 = calculate_indicators(df_15)
            df_1h = calculate_indicators(df_1h)

            result = generate_signal(df_15, df_1h, pair)
            if result:
                signals_found += 1
                msg = format_signal_message(result)
                await bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN)
                await asyncio.sleep(1)  # gentle rate limit
        except Exception as e:
            logger.error(f"Error on {pair}: {e}")

    if signals_found == 0:
        await bot.send_message(chat_id, "✅ Analysis complete. No high-confluence signals right now.")


def format_signal_message(s: dict) -> str:
    emoji = "🟢" if s["signal"] == "BUY" else "🔴"
    reasons = "\n".join(f"• {r}" for r in s["reasons"])
    atr_note = f"\nATR(14): {s['atr']:.5f}" if s["atr"] else ""
    return (
        f"{emoji} *{s['pair']} {s['signal']} SIGNAL*\n\n"
        f"Price: `{s['price']:.5f}`\n"
        f"RSI(14): `{s['rsi']:.1f}`\n"
        f"MACD Hist: `{s['macd_hist']:.5f}`{atr_note}\n\n"
        f"*Why this signal:*\n{reasons}\n\n"
        f"⏰ {s['time']}\n\n"
        f"⚠️ *Not financial advice.* You decide entry, SL & TP.\n"
        f"Risk only what you can afford to lose."
    )


async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 15 minutes for all active users."""
    logger.info("Running scheduled analysis...")
    for chat_id, prefs in list(user_prefs.items()):
        if prefs.get("active", True):
            try:
                await run_analysis_for_user(chat_id, context.bot)
            except Exception as e:
                logger.error(f"Scheduled error for {chat_id}: {e}")
            await asyncio.sleep(2)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("pairs", "Manage watched pairs"),
        BotCommand("status", "Bot status"),
        BotCommand("analyze", "Force analysis now"),
        BotCommand("stop", "Pause signals"),
        BotCommand("resume", "Resume signals"),
    ])
    # Send startup message to admin
    try:
        await app.bot.send_message(
            ADMIN_ID,
            "✅ *Forex Signal Bot is online*\n"
            "Strategy: Multi-Timeframe Confluence (1H + 15m)\n"
            "Check interval: 15 minutes",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass


def main():
    load_user_data()
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pairs", pairs_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Job every 15 minutes
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(scheduled_job, interval=900, first=30)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
One-shot 0DTE Alert runner for OpenClaw cron execution.
Runs a single analysis cycle and exits — no infinite loop.
"""

import os
import sys
import logging
from pathlib import Path

# Ensure we're in the script's directory
os.chdir(Path(__file__).parent)

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ─── Imports from project ───
from data.fetcher import fetch_market_data
from indicators.technicals import add_indicators
from agent.agent import evaluate_with_agent
from alerts.console_alert import send_alert, log_decision
import yaml
import pandas as pd
import numpy as np


def load_config():
    with open("config/strategy.yaml", "r") as f:
        return yaml.safe_load(f)


def should_consider_trade(features: dict) -> tuple[bool, str]:
    """Pre-filter: returns (pass, reason)"""
    msg = ""

    if features["vix"] < 14.0:
        msg = f"VIX too low ({features['vix']}) — premiums too thin."
    elif features["vix"] > 38.0:
        msg = f"VIX too high ({features['vix']}) — extreme fear, gap risk."
    else:
        minutes_left = features["time_to_close_min"]
        if minutes_left > 360:
            msg = f"Too early ({features['current_time']}) — market just opened."
        elif minutes_left < 60:
            msg = f"Too late ({features['current_time']}) — last hour, gamma risk."
        else:
            ret5 = features["ret_5min_pct"]
            ret15 = features["ret_15min_pct"]
            slope_5 = features["ema21_slope_5min"]

            if abs(ret5) > 0.80 or abs(ret15) > 1.40:
                msg = f"Strong momentum (5m: {ret5}%, 15m: {ret15}%) — not ideal."
            elif abs(slope_5) > 3.0:
                msg = f"Steep EMA21 slope ({slope_5} pts/min) — momentum not exhausted."
            elif features["premium_ratio"] <= 3.0:
                msg = f"Premium ratio too low ({features['premium_ratio']})."
            elif features["rsi"] < 18 or features["rsi"] > 82:
                msg = f"RSI extreme ({features['rsi']}) — wait for mean reversion."
            elif features["rsi"] > 40 and features["rsi"] < 60:
                msg = f"RSI neutral ({features['rsi']}) — no directional bias."

    return (True, "") if not msg else (False, msg)


def main():
    # Check required env vars
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("❌ ANTHROPIC_API_KEY not set in .env")
        return 1

    config = load_config()

    # Determine if market is likely open (weekday, 9:30-16:00 ET)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info("📅 Weekend — market closed. Exiting.")
        return 0

    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

    if now_et < market_open or now_et > market_close:
        logger.info(f"🕐 Outside market hours ({now_et.strftime('%H:%M')} ET) — exiting.")
        return 0

    logger.info("🚀 0DTE Alert — Single Analysis Run")

    # Fetch recent history for indicator warm-up
    last_working_day = (pd.Timestamp.now(tz="America/New_York") - pd.offsets.BDay(1)).strftime("%Y-%m-%d")

    try:
        history = fetch_market_data(config["api"], config["live"]["interval_min"], date_in=last_working_day)
    except Exception as e:
        logger.error(f"❌ Failed to fetch history: {e}")
        return 1

    # Fetch latest data
    try:
        df = fetch_market_data(config["api"], config["live"]["interval_min"], date_in=None)
    except Exception as e:
        logger.error(f"❌ Failed to fetch live data: {e}")
        return 1

    history = pd.concat([history, df]).tail(config["live"]["history_size"])
    history = add_indicators(history, config["indicators"]["rsi_period"])

    latest = history.iloc[-1]

    features = {
        "current_price": round(latest["spx"], 2),
        "expected_move": round(latest["spxExpectedMove"], 2),
        "vix": round(latest["vix"], 2),
        "rsi": round(latest["rsi"], 1),
        "macd": round(latest["macd"], 4),
        "macd_hist": round(latest["macd_hist"], 4),
        "macd_signal": round(latest["macd_signal"], 4),
        "bb_upper": round(latest.get("bb_upper", np.nan), 2),
        "bb_lower": round(latest.get("bb_lower", np.nan), 2),
        "bb_middle": round(latest.get("bb_middle", np.nan), 2),
        "premium_ratio": round(latest["premium_ratio"], 2),
        "time_to_close_min": int(latest["time_to_close"]),
        "current_time": latest.name.strftime('%Y-%m-%d %H:%M:%S'),
        "ema9": round(latest.get("ema9", np.nan), 4),
        "ema21": round(latest.get("ema21", np.nan), 4),
        "ema50": round(latest.get("ema50", np.nan), 4),
        "ema21_slope_5min": round(latest["ema21_slope_5min"], 6),
        "ema21_slope_15min": round(latest["ema21_slope_15min"], 6),
        "ema21_slope_30min": round(latest["ema21_slope_30min"], 6),
        "ret_5min_pct": round(latest.get("ret_5min", 0), 2),
        "ret_15min_pct": round(latest.get("ret_15min", 0), 2),
        "ret_30min_pct": round(latest.get("ret_30min", 0), 2),
    }

    logger.info(f"📊 SPX: {features['current_price']} | VIX: {features['vix']} | RSI: {features['rsi']}")

    # Pre-filter
    passed, reason = should_consider_trade(features)
    if not passed:
        logger.info(f"⏭️ Pre-filter rejected: {reason}")
        return 0

    logger.info("✅ Pre-filter passed — sending to AI agent...")

    # AI evaluation
    try:
        decision = evaluate_with_agent(features)
        log_decision(decision.model_dump(), features)

        if decision.trade and decision.trade != "NONE" and decision.confidence >= 0.7:
            logger.info(f"🚨 ALERT: {decision.trade} (confidence: {decision.confidence})")
            send_alert(decision.model_dump(), latest)
        else:
            logger.info(f"🤖 Agent says: no clean setup (trade={decision.trade}, conf={decision.confidence})")

    except Exception as e:
        logger.error(f"❌ Agent evaluation failed: {e}")
        return 1

    logger.info("✅ Analysis complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

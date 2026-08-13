import argparse
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

DEFAULT_STATE_FILE = Path.home() / ".depeg_monitor_state.json"

class DepegMonitor:
    """Monitors stablecoin prices on Binance and Kraken and issues alerts."""

    def __init__(self, telegram_token=None, telegram_chat_id=None, threshold=0.015, spread_threshold=0.01, cooldown_minutes=60, state_path=None):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.threshold = threshold
        self.spread_threshold = spread_threshold
        self.cooldown_seconds = cooldown_minutes * 60
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_FILE
        self.state = self._load_state()

    def _load_state(self):
        if not self.state_path.exists():
            return {"last_alerts": {}}
        try:
            with open(self.state_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, PermissionError):
            # Fall back to empty state if file is corrupted or locked
            return {"last_alerts": {}}

    def _save_state(self):
        try:
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2)
        except PermissionError:
            # Windows locked file or permission issue, skip saving this cycle
            pass

    def _can_alert(self, key):
        last_alert_str = self.state["last_alerts"].get(key)
        if not last_alert_str:
            return True
        
        try:
            last_alert = datetime.fromisoformat(last_alert_str)
            elapsed = (datetime.now(timezone.utc) - last_alert).total_seconds()
            return elapsed >= self.cooldown_seconds
        except ValueError:
            return True

    def _update_alert_time(self, key):
        self.state["last_alerts"][key] = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def send_telegram(self, message):
        if not self.telegram_token or not self.telegram_chat_id:
            print(f"[Console Alert] {message}")
            return

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            response = httpx.post(url, json=payload, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as e:
            sys.stderr.write(f"Failed to send Telegram alert: {e}\n")

    def fetch_binance_price(self, symbol):
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        # print(f"DEBUG: binance raw: {data}")
        return float(data["price"])

    def fetch_kraken_price(self, pair):
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise ValueError(f"Kraken API returned error: {data['error']}")
        
        result = data["result"]
        # Kraken sometimes formats keys uniquely or drops/adds prefixes depending on endpoint query form
        for possible_key in [pair, "USDTZUSD", "USDCZUSD", "XUSDTZUSD", "XUSDCZUSD"]:
            if possible_key in result:
                return float(result[possible_key]["c"][0])
        
        # Fallback to the first dictionary key if none of our standards hit
        key = list(result.keys())[0]
        return float(result[key]["c"][0])

    def processPrice_Data(self):
        """Fetches raw ticks and flags any deviations or major cross-exchange spreads."""
        alerts = []
        prices = {}

        # Fetch Binance feeds
        try:
            prices["binance_usdt_usdc"] = self.fetch_binance_price("USDTUSDC")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            sys.stderr.write(f"Error fetching Binance USDTUSDC: {e}\n")

        # Fetch Kraken feeds
        try:
            prices["kraken_usdt_usd"] = self.fetch_kraken_price("USDTZUSD")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            sys.stderr.write(f"Error fetching Kraken USDTZUSD: {e}\n")

        try:
            prices["kraken_usdc_usd"] = self.fetch_kraken_price("USDCZUSD")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            sys.stderr.write(f"Error fetching Kraken USDCZUSD: {e}\n")

        # 1. Base Peg Deviations
        # Binance USDT/USDC peg test
        if "binance_usdt_usdc" in prices:
            val = prices["binance_usdt_usdc"]
            dev = abs(1.0 - val)
            if dev >= self.threshold:
                alerts.append((
                    "binance_usdt_usdc", 
                    f"⚠️ <b>Binance USDT/USDC</b> deviation: <b>{val:.4f}</b> (dev: {dev*100:.2f}%)"
                ))

        # Kraken USDT/USD peg test
        if "kraken_usdt_usd" in prices:
            val = prices["kraken_usdt_usd"]
            dev = abs(1.0 - val)
            if dev >= self.threshold:
                alerts.append((
                    "kraken_usdt_usd", 
                    f"⚠️ <b>Kraken USDT/USD</b> depeg detected: <b>{val:.4f}</b> (dev: {dev*100:.2f}%)"
                ))

        # Kraken USDC/USD peg test
        if "kraken_usdc_usd" in prices:
            val = prices["kraken_usdc_usd"]
            dev = abs(1.0 - val)
            if dev >= self.threshold:
                alerts.append((
                    "kraken_usdc_usd", 
                    f"⚠️ <b>Kraken USDC/USD</b> depeg detected: <b>{val:.4f}</b> (dev: {dev*100:.2f}%)"
                ))

        # 2. Cross-Exchange Discrepancy Detection
        # Comparing Binance synthetic USDT rate vs Kraken USDT/USD rate
        # Binance USDT price in real fiat USD is simulated by assuming USDC is exactly $1.0
        # FIXME: During major USDC depegs, synthetic cross-rates can output false positives
        if "binance_usdt_usdc" in prices and "kraken_usdt_usd" in prices:
            b_usdt = 1.0 / prices["binance_usdt_usdc"] if prices["binance_usdt_usdc"] > 0 else 0
            k_usdt = prices["kraken_usdt_usd"]
            if b_usdt > 0 and k_usdt > 0:
                spread = abs(b_usdt - k_usdt) / k_usdt
                if spread >= self.spread_threshold:
                    alerts.append((
                        "usdt_cross_spread",
                        f"🚨 <b>USDT Spread Alert</b> between exchanges!\n"
                        f"Kraken: <b>${k_usdt:.4f}</b>\n"
                        f"Binance (Synthetic): <b>${b_usdt:.4f}</b>\n"
                        f"Spread: <b>{spread*100:.2f}%</b>"
                    ))

        # Fire rate-limited alerts
        for alert_key, msg in alerts:
            if self._can_alert(alert_key):
                self.send_telegram(msg)
                self._update_alert_time(alert_key)
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Alert suppressed for {alert_key} (cooldown active)")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor stablecoin prices on Binance and Kraken and trigger alerts.",
        epilog="Usage example: depeg_monitor.py --threshold 0.015 --spread-threshold 0.01 --cooldown 45"
    )
    parser.add_argument("--telegram-token", help="Telegram Bot Token for automated messaging")
    parser.add_argument("--telegram-chat-id", help="Telegram Chat ID")
    parser.add_argument("--threshold", type=float, default=0.015, help="Deviation threshold from $1.0 (e.g. 0.015 for 1.5%)")
    parser.add_argument("--spread-threshold", type=float, default=0.01, help="Cross-exchange discrepancy threshold (e.g. 0.01 for 1%)")
    parser.add_argument("--cooldown", type=int, default=60, help="Notification cooldown in minutes to limit spam")
    parser.add_argument("--state-file", help="Custom path to state tracking JSON file")
    parser.add_argument("--loop", type=int, default=0, help="Run continuously checking every N seconds. If 0, runs once.")

    args = parser.parse_args()

    if args.telegram_token and not args.telegram_chat_id:
        sys.stderr.write("Error: --telegram-chat-id is required if --telegram-token is specified.\n")
        sys.exit(1)

    monitor = DepegMonitor(
        telegram_token=args.telegram_token,
        telegram_chat_id=args.telegram_chat_id,
        threshold=args.threshold,
        spread_threshold=args.spread_threshold,
        cooldown_minutes=args.cooldown,
        state_path=args.state_file
    )

    if args.loop > 0:
        print(f"Starting monitor loop. Checking feeds every {args.loop} seconds...")
        while True:
            monitor.processPrice_Data()
            time.sleep(args.loop)
    else:
        monitor.processPrice_Data()

if __name__ == "__main__":
    main()

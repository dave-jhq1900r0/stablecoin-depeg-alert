# stablecoin-depeg-alert

I keep some capital in stablecoins (mostly USDC and USDT) and wanted a dead-simple, reliable script to watch their prices directly from exchange APIs. I do not want to pay for a SaaS or configure heavy monitoring stacks just to watch a few ticker endpoints.

This script runs in a loop, fetches prices from Binance and Kraken, checks them against your thresholds, and alerts you via Telegram if a stablecoin drops too low or climbs too high. It also tracks the spread between exchanges to catch early signs of panic.

## Installation

Clone the repository and install the dependencies:

```bash
pip install -r requirements.txt
```

## Usage

To run the monitor with default thresholds (alerts if any stablecoin drops below 0.985 or goes above 1.015):

```bash
python depeg_monitor.py --telegram-token YOUR_TOKEN --telegram-chat YOUR_CHAT_ID
```

You can customize the watched tokens and thresholds:

```bash
python depeg_monitor.py \
  --tokens USDT,USDC,DAI \
  --low-threshold 0.99 \
  --high-threshold 1.01 \
  --interval 30 \
  --telegram-token YOUR_TOKEN \
  --telegram-chat YOUR_CHAT_ID
```

The script keeps a small state file (`depeg_state.json`) in the current directory to remember when it last sent an alert. This ensures you do not get spammed with hundreds of notifications if a stablecoin stays depegged for hours. By default, it rate-limits alerts to once every 15 minutes per token.

<!-- checked: 2026-09-25 -->

"""
Price Monitor using BeautifulSoup
Monitors a webpage for price changes and sends alerts via email or desktop notification.

Usage:
    python price_monitor.py

Requirements:
    pip install requests beautifulsoup4 lxml

Optional (for email alerts):
    Configure SMTP settings in the Config section below.
"""

import requests
import smtplib
import json
import time
import logging
import re
import os
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─────────────────────────────────────────────
# Configuration — edit these before running
# ─────────────────────────────────────────────
@dataclass
class Config:
    # --- Target page ---
    url: str = "https://example.com/product-page"   # URL to monitor

    # --- CSS selector for the price element ---
    # Examples:
    #   Amazon:  "span.a-price > span.a-offscreen"
    #   eBay:    "div.x-price-primary > span"
    #   Generic: ".price", "#product-price", "[data-price]"
    price_selector: str = ".price"

    # --- Polling ---
    check_interval_seconds: int = 3600   # How often to check (default: every hour)

    # --- Alert threshold ---
    # Alert only when price drops by at least this percentage (0 = any drop)
    min_drop_percent: float = 0.0

    # --- Email alerts (optional) ---
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "your@gmail.com"
    smtp_password: str = "your_app_password"   # Use an App Password, not your login password
    alert_recipients: list = field(default_factory=lambda: ["you@example.com"])

    # --- Desktop notifications (optional, requires 'plyer') ---
    desktop_notify: bool = True

    # --- State persistence ---
    state_file: str = "price_history.json"

    # --- HTTP request headers ---
    headers: dict = field(default_factory=lambda: {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })


# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("price_monitor.log"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Price fetching
# ─────────────────────────────────────────────

def fetch_price(config: Config) -> Optional[float]:
    """Fetch the page and extract the price using the configured CSS selector."""
    try:
        response = requests.get(config.url, headers=config.headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        log.error("Request failed: %s", exc)
        return None

    soup = BeautifulSoup(response.text, "lxml")
    element = soup.select_one(config.price_selector)

    if element is None:
        log.warning("Price element not found with selector: %r", config.price_selector)
        log.debug("Page snippet:\n%s", soup.prettify()[:2000])
        return None

    raw_text = element.get_text(strip=True)
    return parse_price(raw_text)


def parse_price(text: str) -> Optional[float]:
    """Extract a numeric price from a string like '$1,299.99' or '€ 849,00'."""
    # Remove currency symbols, whitespace; normalise European comma decimals
    cleaned = re.sub(r"[^\d.,]", "", text)

    # Handle formats like "1.299,99" (European) vs "1,299.99" (US)
    if re.search(r",\d{2}$", cleaned) and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        log.error("Could not parse price from text: %r", text)
        return None


# ─────────────────────────────────────────────
# State persistence
# ─────────────────────────────────────────────

def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────
# Alerting
# ─────────────────────────────────────────────

def send_email_alert(config: Config, old_price: float, new_price: float) -> None:
    """Send an email notification about the price drop."""
    subject = f"💰 Price Drop Alert: ${new_price:.2f} (was ${old_price:.2f})"
    drop_pct = (old_price - new_price) / old_price * 100

    body = (
        f"Good news! A price drop was detected.\n\n"
        f"  URL        : {config.url}\n"
        f"  Old price  : ${old_price:.2f}\n"
        f"  New price  : ${new_price:.2f}\n"
        f"  Savings    : ${old_price - new_price:.2f}  ({drop_pct:.1f}% off)\n"
        f"  Detected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Check it out before it changes!\n"
    )

    msg = MIMEMultipart()
    msg["From"] = config.smtp_user
    msg["To"] = ", ".join(config.alert_recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.sendmail(config.smtp_user, config.alert_recipients, msg.as_string())
        log.info("Email alert sent to %s", config.alert_recipients)
    except smtplib.SMTPException as exc:
        log.error("Failed to send email: %s", exc)


def send_desktop_notification(old_price: float, new_price: float) -> None:
    """Send a desktop notification (requires the 'plyer' package)."""
    try:
        from plyer import notification  # type: ignore
        notification.notify(
            title="💰 Price Drop!",
            message=f"${new_price:.2f}  (was ${old_price:.2f})",
            timeout=10,
        )
    except ImportError:
        log.warning("Desktop notifications need 'plyer': pip install plyer")
    except Exception as exc:
        log.warning("Desktop notification failed: %s", exc)


def trigger_alerts(config: Config, old_price: float, new_price: float) -> None:
    drop_pct = (old_price - new_price) / old_price * 100

    log.info(
        "🔔 PRICE DROP: $%.2f → $%.2f  (%.1f%% off)",
        old_price, new_price, drop_pct,
    )

    if config.desktop_notify:
        send_desktop_notification(old_price, new_price)

    if config.email_enabled:
        send_email_alert(config, old_price, new_price)


# ─────────────────────────────────────────────
# Main monitoring loop
# ─────────────────────────────────────────────

def monitor(config: Config) -> None:
    log.info("Starting price monitor for: %s", config.url)
    log.info("Selector : %s", config.price_selector)
    log.info("Interval : %d seconds", config.check_interval_seconds)

    state = load_state(config.state_file)
    key = config.url  # Use URL as the key so multiple URLs can share one file

    while True:
        log.info("Checking price…")
        current_price = fetch_price(config)

        if current_price is not None:
            log.info("Current price: $%.2f", current_price)

            record = state.get(key, {})
            previous_price: Optional[float] = record.get("price")
            highest_price: float = record.get("highest", current_price)
            lowest_price: float = record.get("lowest", current_price)

            # Update historical stats
            highest_price = max(highest_price, current_price)
            lowest_price = min(lowest_price, current_price)

            state[key] = {
                "price": current_price,
                "highest": highest_price,
                "lowest": lowest_price,
                "last_checked": datetime.now().isoformat(),
                "history": record.get("history", []) + [
                    {"price": current_price, "ts": datetime.now().isoformat()}
                ],
            }
            save_state(config.state_file, state)

            # Decide whether to alert
            if previous_price is not None and current_price < previous_price:
                drop_pct = (previous_price - current_price) / previous_price * 100
                if drop_pct >= config.min_drop_percent:
                    trigger_alerts(config, previous_price, current_price)
                else:
                    log.info(
                        "Price dropped %.1f%% but threshold is %.1f%% — no alert",
                        drop_pct, config.min_drop_percent,
                    )
            elif previous_price is None:
                log.info("First check — baseline price saved: $%.2f", current_price)
            else:
                log.info("No drop (previous: $%.2f)", previous_price)

        log.info("Next check in %d seconds…\n", config.check_interval_seconds)
        time.sleep(config.check_interval_seconds)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    cfg = Config(
        # ── Customise these three lines ──────────────────────────────────
        url="https://example.com/product-page",
        price_selector=".price",          # CSS selector for the price element
        check_interval_seconds=3600,      # Check every hour
        # ─────────────────────────────────────────────────────────────────
        min_drop_percent=1.0,             # Alert on drops ≥ 1%
        desktop_notify=True,
        email_enabled=False,              # Set True and fill credentials to enable
    )

    try:
        monitor(cfg)
    except KeyboardInterrupt:
        log.info("Monitor stopped by user.")

"""
PrizePicks Discount Monitor
----------------------------
Polls @PrizePicks on X for new tweets, uses GPT-4o to detect discounts
(including image analysis), and sends a Telegram message when one is found.
"""

import os
import time
import logging
from pathlib import Path
from dotenv import load_dotenv
import requests
import tweepy
from openai import OpenAI

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

X_BEARER_TOKEN      = os.environ["X_BEARER_TOKEN"]
OPENAI_API_KEY      = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_LOG_CHAT_ID = os.environ.get("TELEGRAM_LOG_CHAT_ID")

TARGET_USERNAME = "PrizePicks"
POLL_INTERVAL   = 15 * 60   # seconds (15 min — free X API minimum)
STATE_FILE      = Path("last_tweet_id.txt")

# ── Logging ───────────────────────────────────────────────────────────────────

class TelegramLogHandler(logging.Handler):
    """Sends log records to a Telegram chat with notifications silenced."""

    MAX_LENGTH = 4000

    def __init__(self, bot_token: str, chat_id: str):
        super().__init__()
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)[:self.MAX_LENGTH]
            requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "disable_notification": True,
                },
                timeout=10,
            )
        except Exception:
            pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

if TELEGRAM_LOG_CHAT_ID:
    _tg_handler = TelegramLogHandler(TELEGRAM_BOT_TOKEN, TELEGRAM_LOG_CHAT_ID)
    _tg_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(_tg_handler)

# ── State persistence ─────────────────────────────────────────────────────────

def load_last_id() -> str | None:
    if STATE_FILE.exists():
        val = STATE_FILE.read_text().strip()
        return val or None
    return None

def save_last_id(tweet_id: str) -> None:
    STATE_FILE.write_text(str(tweet_id))

# ── X API ─────────────────────────────────────────────────────────────────────

def fetch_new_tweets(client: tweepy.Client, user_id: str, since_id: str | None):
    """Return (tweets, media_map) for any tweets newer than since_id."""
    kwargs = dict(
        id=user_id,
        max_results=10,
        exclude=["replies", "retweets"],
        tweet_fields=["created_at", "text", "attachments"],
        expansions=["attachments.media_keys"],
        media_fields=["url", "preview_image_url", "type"],
    )
    if since_id:
        kwargs["since_id"] = since_id

    resp = client.get_users_tweets(**kwargs)

    media_map: dict[str, str] = {}
    if resp.includes and "media" in resp.includes:
        for m in resp.includes["media"]:
            url = m.get("url") or m.get("preview_image_url")
            if url:
                media_map[m["media_key"]] = url

    return resp.data or [], media_map

# ── OpenAI discount detection ─────────────────────────────────────────────────

def is_discount(tweet_text: str, image_urls: list[str]) -> bool:
    """
    Ask GPT-4o whether the tweet contains a discount / promo code / deal.
    Passes any attached images for vision analysis.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    content: list[dict] = []

    for url in image_urls:
        content.append({
            "type": "image_url",
            "image_url": {"url": url},
        })

    content.append({
        "type": "text",
        "text": (
            "You are analyzing a tweet from the PrizePicks official account.\n\n"
            f"Tweet text:\n{tweet_text}\n\n"
            "Does this tweet offer a discount, promo code, deposit bonus, "
            "free entry, or any other special deal for users?\n"
            "Consider both the text AND any image shown above.\n"
            "For example, an image that says DISCOUNT or Taco Tuesday.\n"
            "Reply with ONLY the word 'yes' or 'no'."
        ),
    })

    resp = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=5,
        messages=[{"role": "user", "content": content}],
    )
    answer = resp.choices[0].message.content.strip().lower()
    return answer == "yes"

# ── Telegram notification ─────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    resp.raise_for_status()
    log.info("Telegram message sent.")

# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    x_client = tweepy.Client(bearer_token=X_BEARER_TOKEN, wait_on_rate_limit=True)

    # Resolve username → numeric user ID (cached implicitly by Tweepy)
    resp = x_client.get_user(username=TARGET_USERNAME)
    if not resp.data:
        raise RuntimeError(f"Could not find X user @{TARGET_USERNAME}")
    user_id = str(resp.data.id)
    log.info("Monitoring @%s (id=%s)", TARGET_USERNAME, user_id)

    while True:
        try:
            since_id = load_last_id()
            tweets, media_map = fetch_new_tweets(x_client, user_id, since_id)

            if tweets:
                # Process oldest-first so we can save IDs progressively
                for tweet in reversed(tweets):
                    image_urls = []
                    if tweet.attachments and "media_keys" in tweet.attachments:
                        for key in tweet.attachments["media_keys"]:
                            if key in media_map:
                                image_urls.append(media_map[key])

                    preview = tweet.text[:80].replace("\n", " ")
                    log.info("Checking: %s%s", preview, "…" if len(tweet.text) > 80 else "")

                    if is_discount(tweet.text, image_urls):
                        log.info("  → Discount detected! Sending Telegram message.")
                        msg = (
                            f"🎯 PrizePicks deal alert!\n\n"
                            f"{tweet.text}\n\n"
                            f"https://x.com/PrizePicks"
                        )
                        send_telegram(msg)
                    else:
                        log.info("  → Not a discount.")

                    save_last_id(tweet.id)
            else:
                log.info("No new tweets.")

        except tweepy.TweepyException as e:
            log.error("X API error: %s", e)
        except Exception as e:
            log.exception("Unexpected error: %s", e)

        log.info("Sleeping %d min…", POLL_INTERVAL // 60)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

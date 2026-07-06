from pathlib import Path
import os
from dotenv import load_dotenv
import tweepy

# override=True: force .env values, ignoring any shadowing env vars
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

client = tweepy.Client(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
)
try:
    me = client.get_me()
    print(f"SUCCESS — .env credentials are VALID (authenticated as @{me.data.username})")
    print("=> If the bot still 401s, environment variables are shadowing your .env.")
except tweepy.errors.Unauthorized:
    print("FAILED (401) — the values in .env are themselves invalid/stale.")
    print("=> Regenerate the OAuth 1.0a keys in the X Developer Portal and update .env.")
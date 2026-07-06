# News-to-Tweet Bot

Fetches [Google News (World edition) RSS](https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en), ranks stories by "trendiness", composes tweet copy, and posts the top story to X/Twitter — **once per hour, fully autonomous**, via GitHub Actions (no server needed).

## How it works

```
fetch RSS  →  cluster & rank  →  freshness check  →  dedup  →  compose tweet  →  post to X
```

**Ranking** (no engagement data exists in RSS, so trendiness is approximated):

| Signal | Weight | What it measures |
|---|---|---|
| Cross-source repetition | 40% | How many outlets covered the same event (headlines clustered by token overlap) |
| Recency | 35% | Exponential decay, 3-hour half-life |
| Source prominence | 25% | Reuters/AP/BBC tier down to unknown outlets |

The composite is multiplied by a **style penalty** that down-ranks question/explainer/opinion/live-blog headlines in favor of hard news, and a **freshness constraint** guarantees the posted story is under 1 hour old (progressively widened to 2h/3h/6h if the pull has nothing that fresh).

**Tweet composition** (free, no LLM): the headline is tightened AP-style (filler phrases stripped, wordy constructions contracted), attributed to its source, and — when multiple outlets covered the story — expanded with how other outlets worded it:

> Massive earthquake strikes off Japan coast, tsunami warning issued (Reuters)
>
> Also reported: "Japan orders coastal evacuations after 7.4 magnitude quake" (BBC)

Nothing is ever invented — every character comes from an actual published headline. Optionally, setting `ANTHROPIC_API_KEY` switches to LLM rephrasing instead (paid).

**Safeguards**: 48h duplicate prevention (by link *and* headline similarity, committed back to the repo so it survives ephemeral runners), retry with backoff on rate limits and server errors, no retry on auth failures, char-budget validation using X's real counting rules, and a failed post is never recorded as posted.

## Deploy on GitHub Actions (hourly, autonomous)

### 1. Create the X app credentials
In the [X Developer Portal](https://developer.twitter.com/) → your app → **Keys and tokens**:
- Set app permissions to **Read and Write** *first*, then generate/regenerate the **OAuth 1.0a** Access Token & Secret. (Tokens generated before enabling write are read-only forever.)
- You need the OAuth **1.0a** section (Consumer Key/Secret + Access Token/Secret) — *not* the OAuth 2.0 Client ID/Secret.

> Note: X's API is pay-per-use for new developers; posting costs money per tweet. Check current pricing before running hourly.

### 2. Push this repo to GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 3. Add repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**, add all four:

| Secret name | Value |
|---|---|
| `X_API_KEY` | OAuth 1.0a Consumer Key |
| `X_API_SECRET` | OAuth 1.0a Consumer Secret |
| `X_ACCESS_TOKEN` | OAuth 1.0a Access Token |
| `X_ACCESS_TOKEN_SECRET` | OAuth 1.0a Access Token Secret |

(Optional: `ANTHROPIC_API_KEY` for paid LLM rephrasing.)

### 4. Enable and test the workflow
- Repo → **Actions** tab → enable workflows if prompted.
- Select **Hourly tweet** → **Run workflow** → tick **Dry run** → run. Check the logs: you should see the composed tweet without anything being posted.
- Run again without dry-run to post for real. After that, it runs automatically every hour.

### Things to know about GitHub Actions scheduling
- Cron is **UTC** and **not exact** — runs can be delayed by several minutes under load. The bot's freshness logic is unaffected.
- **Scheduled workflows are disabled automatically after 60 days without repository activity.** Conveniently, this bot commits `data/posted_history.json` after each post, which counts as activity — but if the bot stops posting for 60 days (or you fork-and-forget), re-enable it in the Actions tab.
- The workflow commits the dedup history back to the repo (`[skip ci]` tagged), which is why it needs `contents: write` permission.

## Run locally instead

```bash
pip install -r requirements.txt
cp .env.example .env       # then fill in your credentials
python main.py --dry-run --once   # test without posting
python main.py --once             # single cycle (for your own cron)
python main.py                    # built-in hourly scheduler (Europe/Paris)
```

## Run the tests

```bash
pip install pytest
pytest -v
```

## Project structure

```
├── .github/workflows/
│   ├── hourly-tweet.yml   # hourly posting (cron + manual trigger w/ dry-run)
│   └── ci.yml             # tests on push/PR
├── bot/
│   ├── config.py          # every tunable in one place
│   ├── models.py          # Article / RankedStory / PostedEntry
│   ├── fetcher.py         # RSS fetch + parse (+ optional link resolution)
│   ├── ranker.py          # clustering, scoring, style penalty, freshness
│   ├── rephraser.py       # tweet composition (free + optional LLM)
│   ├── history.py         # dedup store
│   ├── poster.py          # X client + retry-aware posting
│   ├── pipeline.py        # one full cycle
│   └── cli.py             # argparse + local scheduler
├── data/
│   └── posted_history.json  # dedup state (committed by the workflow)
├── tests/
├── main.py
└── requirements.txt
```

## Configuration

Everything tunable lives in [`bot/config.py`](bot/config.py): feed URL, scoring weights, freshness windows, tweet length (288), whether to include links (off by default), dedup lookback, retry policy, source prominence tiers.

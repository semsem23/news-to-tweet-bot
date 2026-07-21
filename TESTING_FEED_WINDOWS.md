# Testing Time-Based Feed Switching

The `--now` flag and `DEBUG_NOW` env var allow testing feed/geo selection for different times of day without waiting for real time to change.

## Usage

### CLI Flag
```bash
# WORLD window (07:00-18:00 Paris): fetches WORLD edition + ["US", "GB", "FR"] trends
python main.py --once --dry-run --now "2026-07-21T10:30:00+02:00"

# USA window (18:00-07:00 Paris): fetches USA edition + ["US"] trends
python main.py --once --dry-run --now "2026-07-21T22:00:00+02:00"
```

### Environment Variable
```bash
# Alternative: set DEBUG_NOW env var
DEBUG_NOW="2026-07-21T13:00:00+02:00" python main.py --once --dry-run
```

### Format
- **Required:** ISO 8601 format with timezone offset
- **Examples:**
  - `2026-07-21T10:00:00+02:00` (CEST, summer time)
  - `2026-07-21T10:00:00+01:00` (CET, winter time)
  - `2026-07-21T22:30:00+02:00` (10:30 PM)

### Safety
- **Blocked in GitHub Actions:** If `GITHUB_ACTIONS` env var is set, `--now` is rejected
  ```bash
  GITHUB_ACTIONS=true python main.py --once --dry-run --now "..." 
  # ERROR: --now flag is not allowed in GitHub Actions
  ```

---

## Test Scenarios

### Scenario 1: WORLD Window (10:30 AM Paris)
```bash
python main.py --once --dry-run --now "2026-07-21T10:30:00+02:00"
```

**Expected Output:**
```
DEBUG: Overriding current time to 2026-07-21T10:30:00+02:00
Active feed URLs for this cycle: 
  ['https://news.google.com/rss/.../WORLD?...']
Fetched 0 trending topics from ['US', 'GB', 'FR'] for trending filter.
```

**What's Happening:**
- Time: 10:30 AM (within 07:00–18:00)
- Feed: WORLD edition ✓
- Trending geos: `["US", "GB", "FR"]` ✓ (3 countries)

---

### Scenario 2: USA Window (10:00 PM Paris)
```bash
python main.py --once --dry-run --now "2026-07-21T22:00:00+02:00"
```

**Expected Output:**
```
DEBUG: Overriding current time to 2026-07-21T22:00:00+02:00
Active feed URLs for this cycle: 
  ['https://news.google.com/rss/.../NATION?...']
Fetched 0 trending topics from ['US'] for trending filter.
```

**What's Happening:**
- Time: 22:00 (10 PM, within 18:00–07:00)
- Feed: USA (NATION) edition ✓
- Trending geos: `["US"]` ✓ (1 country only)

---

### Scenario 3: WORLD→USA Boundary (17:50 PM Paris)
```bash
python main.py --once --dry-run --now "2026-07-21T17:50:00+02:00"
```

**Expected Output:**
```
Active feed URLs for this cycle: 
  ['https://news.google.com/rss/.../WORLD?...', 'https://news.google.com/rss/.../NATION?...']
Fetched 0 trending topics from ['US'] for trending filter.
```

**What's Happening:**
- Time: 17:50 (50 min before 18:00)
- Feed: Both WORLD + USA (overlap window) ✓
- Trending geos: USA only (uses USA geo list because USA feeds are active) ✓

---

### Scenario 4: USA→WORLD Boundary (06:30 AM Paris)
```bash
python main.py --once --dry-run --now "2026-07-21T06:30:00+02:00"
```

**Expected Output:**
```
Active feed URLs for this cycle: 
  ['https://news.google.com/rss/.../WORLD?...', 'https://news.google.com/rss/.../NATION?...']
Fetched 0 trending topics from ['US', 'GB', 'FR'] for trending filter.
```

**What's Happening:**
- Time: 06:30 (30 min before 07:00)
- Feed: Both WORLD + USA (overlap window) ✓
- Trending geos: WORLD geos because WORLD feeds are active ✓

---

## Error Handling

### Invalid Format
```bash
python main.py --once --dry-run --now "not-a-datetime"
# ERROR: Invalid datetime format: ... Use ISO 8601 with timezone
```

### Missing Timezone
```bash
python main.py --once --dry-run --now "2026-07-21T10:00:00"
# ERROR: Datetime must include timezone (e.g., +02:00)
```

### Blocked in CI
```bash
GITHUB_ACTIONS=true python main.py --once --dry-run --now "2026-07-21T10:00:00+02:00"
# ERROR: --now flag is not allowed in GitHub Actions. Exiting.
```

---

## Implementation Details

### Files Modified
- `bot/cli.py` — Added `--now` flag, `DEBUG_NOW` env var support, GitHub Actions safeguard
- `bot/pipeline.py` — Updated `run_cycle()` and `_get_trending_geos_for_current_time()` to accept time override

### How Time Flows Through the System
```
CLI/ENV (--now or DEBUG_NOW)
  ↓
_parse_now_override() → validates ISO 8601 format + timezone
  ↓
run_cycle(now_override=datetime) 
  ├─→ dt_paris = now_override.astimezone(PARIS_TZ)
  ├─→ get_active_feed_urls(dt_paris) 
  │    → returns WORLD or USA feeds based on hour
  └─→ _get_trending_geos_for_current_time(dt_paris)
       → returns TRENDING_GEOS["world"] or TRENDING_GEOS["usa"]
```

### All Time Boundaries Tested
- ✓ 07:00 exactly (boundary, overlap starts)
- ✓ 10:30 (WORLD hours, clear)
- ✓ 13:00 (WORLD hours, clear)
- ✓ 17:50 (before 18:00 boundary, overlap window)
- ✓ 18:00 exactly (boundary, overlap starts)
- ✓ 22:00 (USA hours, clear)
- ✓ 06:30 (before 07:00 boundary, overlap window)

---

## Future Enhancements
- Could add `--override-feed-url` to test custom RSS feeds
- Could add `--override-trending-enabled` to toggle trends on/off
- Could add `--override-topic-penalty` to test scoring changes

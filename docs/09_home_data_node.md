# 09 Home Data Node Deployment (`deploy/home_data_node/`)

> Goal: **use a small Linux box at home (HP 800G4 / N100 / Raspberry Pi 5 all work)**
> as the primary data node — **a single systemd job `quant-daily`** automatically does the following every day:
> JoinQuant check-in → incremental JQ sync → news / theme archive → **three-year LRU retention purge** → (optional) R2 cold backup.
>
> GitHub Actions does **not** do the check-in or the data sync (non-mainland-China IPs are blocked by JoinQuant).

Corresponding code: `deploy/home_data_node/`.
Quick start: `deploy/home_data_node/README.md`.
This document explains **why it is designed this way**, **how to operate it**, and **how to migrate it**.

---

## 1. Why a "Data Node" Is Needed

### 1.1 Data Source Constraints

JoinQuant's `jqdatasdk` uses **Thrift RPC to `39.107.190.114:7000`**,
which is raw TCP rather than HTTP. When going through a corporate proxy (Fortinet / zero-trust):

- Companies that only allow HTTP/HTTPS → the thrift port is blocked and the sync fails
- Companies with SSL inspection → even a UDP hole-punching VPN may get blocked

So **the machine that can reach JoinQuant must have a "clean public-internet egress"** — the cheapest option is home broadband.

### 1.2 Needs of the Three Data Consumers

| Who | What it needs | Where it gets data |
|----|---------|-------------|
| **The home mini-PC itself** | Run the sync, run backtest R&D | Local SQLite |
| **Office Linux box** | Run backtest R&D, view backtest results | Pull from R2 (no VPN needed) |
| **GitHub Actions CI** | Only push / manually triggered backtest drift checks (optional R2) | Not the main pipeline |
| **Windows QMT gateway** | Trading only, no data | Connects directly to the market-data API through xtquant_gateway |

The solution: **the home node pushes data to R2, and everyone else pulls from R2**. R2 goes over HTTPS,
and zero-trust won't block it either (that would mean not even an ordinary browser could get online).

---

## 2. Hardware Reference

| Model | Reference price | Idle power | Full-load power | Notes |
|------|-------|---------|---------|------|
| **HP EliteDesk 800 G4 mini** (the one you already have) | Used ¥600–1500 | 8–15 W | 30–50 W | More than enough, an old veteran |
| **N100 fanless mini-PC** | New ¥800–1200 | 6–10 W | 20–30 W | First choice for a new purchase: quiet, 10W idle |
| **Raspberry Pi 5 8G + 512G SSD** | ¥700 | 3–6 W | 12–15 W | Lowest power, but the sync is 30% slower |
| **Used ThinkCentre M75q** | Used ¥800 | 10 W | 35 W | AMD 4750GE, great value for money |

### Electricity Cost Estimate (¥0.6/kWh)

| Always on 24×7, idle 12 W | About 105 kWh per year | About **¥63/year** |
| Always on 24×7, idle 15 W | About 130 kWh per year | About **¥78/year** |

Conclusion: **don't bother with sleep/wake; leave it on 24×7**.

---

## 3. Deployment (Detailed Version)

### 3.1 Prerequisites

- A Linux machine (Ubuntu 22.04 / Debian 12 / Fedora all work, as long as it uses systemd)
- Your home network can reach `39.107.190.114:7000` (it can by default, unless you changed your ISP firewall)
- A Cloudflare account (the free tier is enough)

### 3.2 Install Cloudflare R2

1. Log in to <https://dash.cloudflare.com> → left sidebar **R2 Object Storage**
2. On first entry you will be asked to add a credit card; **you will not be charged within the free tier** (10 GB storage + no egress fees)
3. **Create bucket** → name it `quant-data` → any region
4. Left sidebar **Manage R2 API tokens** → **Create API token**:
   - Permission: **Object Read & Write**
   - Specify bucket: `quant-data`
   - TTL: forever
5. Note down:
   - `Access Key ID`
   - `Secret Access Key`
   - `Endpoint` (of the form `https://<account_id>.r2.cloudflarestorage.com`)

### 3.3 Clone the Repo & Fill in `.env`

```bash
# Home machine
git clone git@github.com:com0do/quant.git ~/quant
cd ~/quant

# Top-level .env: JoinQuant account + optional proxy
cp .env.example .env
$EDITOR .env
# Fill in at least:
#   JQ_USERNAME=xxxxxxxxxxx     # phone number registered with JoinQuant
#   JQ_PASSWORD=xxxxxxxxxx      # JoinQuant login password

# Deployment package .env: R2 + scheduling
cd deploy/home_data_node
cp .env.example .env
$EDITOR .env
# Fill in at least:
#   R2_ENABLE=true
#   R2_ACCESS_KEY_ID=<the one you just got>
#   R2_SECRET_ACCESS_KEY=<the one you just got>
#   R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com
```

### 3.4 Run setup.sh

```bash
cd ~/quant/deploy/home_data_node
sudo bash setup.sh
```

`setup.sh` is idempotent; running it repeatedly has no side effects. What it does:

1. `apt install jq rclone sqlite3 xz-utils`
2. `curl -LsSf https://astral.sh/uv/install.sh | sh` (if uv is not installed)
3. `uv sync` to install the Python dependencies
4. (Optional) install Tailscale and run `tailscale up`
5. Write the rclone R2 remote to `~/.config/rclone/rclone.conf`
6. Generate 4 systemd units (2 services + 2 timers) and drop them into `/etc/systemd/system/`
7. `systemctl enable --now quant-daily.timer` (**the only scheduled job**)

### 3.5 Verification

```bash
systemctl list-timers | grep quant

# Run the full daily job immediately (check-in + sync + three-year purge + optional R2)
sudo systemctl start quant-daily.service
sudo journalctl -fu quant-daily

# Purge report
cat output/home_data_node_retention_report.json
```

If everything runs through, you will have:

- **Home primary DB**: `~/quant/data/runtime/*.db`, updated automatically every trading day
- **R2 cold backup**: `r2:quant-data/home/YYYYMMDD/*.db.xz`, one copy per day + a `latest/` alias
- **Status**: `~/quant/output/home_data_node_sync_status.json`, overwritten after every sync,
  recording the day's window and each DB's min/max date and row count

---

## 4. How Other Machines Pull the Data

### 4.1 Office / Your Other Linux Machine

```bash
git clone git@github.com:com0do/quant.git ~/quant
cd ~/quant
uv sync

# You only need to set the R2 variables in the top-level .env (JQ_USERNAME / JQ_PASSWORD are not needed, since this machine does not sync)
cp .env.example .env
$EDITOR .env
# Fill in: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT / R2_BUCKET=quant-data

# Install rclone and configure the remote (using the same rclone.conf as at home also works)
sudo apt install rclone xz-utils sqlite3

# Pull
bash deploy/home_data_node/bin/pull-from-r2.sh

# After that, data/runtime/ has the latest SQLite files
uv run python main.py --mode backtest --config config/final_freeze_2025.toml
```

### 4.2 GitHub Actions

In the repo **Settings → Secrets and variables → Actions**, add:

- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`
- `R2_BUCKET` (optional, defaults to `quant-data`)

Then add a step to the workflow:

```yaml
- name: Install rclone
  run: |
    sudo apt-get install -y -qq rclone xz-utils sqlite3

- name: Pull data from R2
  env:
    R2_ACCESS_KEY_ID:     ${{ secrets.R2_ACCESS_KEY_ID }}
    R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
    R2_ENDPOINT:          ${{ secrets.R2_ENDPOINT }}
    R2_BUCKET:            quant-data
  run: |
    mkdir -p ~/.config/rclone
    cat > ~/.config/rclone/rclone.conf <<EOF
    [r2]
    type = s3
    provider = Cloudflare
    access_key_id = $R2_ACCESS_KEY_ID
    secret_access_key = $R2_SECRET_ACCESS_KEY
    endpoint = $R2_ENDPOINT
    region = auto
    EOF
    bash deploy/home_data_node/bin/pull-from-r2.sh
```

`.github/workflows/backtest_check.yml` already includes this section;
you only need to fill in the 3 secrets in Settings and it runs automatically.

---

## 5. Migrating to a New Machine (future HP 800G4 → N100)

**Core principle**: all mutable state lives in `~/quant/`; `deploy/home_data_node/` is just scripts.

```bash
# 1. Old machine: pack
cd ~
tar --exclude='quant/.venv' --exclude='quant/__pycache__' \
    -czf /tmp/quant.tar.gz quant

# 2. scp to the new machine
scp /tmp/quant.tar.gz newhost:/tmp/

# 3. New machine: unpack + rerun setup
tar xzf /tmp/quant.tar.gz -C ~/
cd ~/quant/deploy/home_data_node
sudo bash setup.sh          # automatically detects the existing .env, only reinstalls the systemd units and dependencies

# 4. Old machine: disable the systemd units to avoid double writes
# Old machine: sudo bash deploy/home_data_node/uninstall.sh
```

If you don't want to tar (after all, the SQLite files are several hundred MB), you can also:

- Only clone the repo and copy `.env`, then run `bash pull-from-r2.sh` to pull the data from R2
- Prerequisite: first manually run `sudo systemctl start quant-r2-backup.service` on the old machine to make sure R2 has the latest version

---

## 6. What the Daily Job `quant-daily` Does

`deploy/home_data_node/bin/run-daily.sh` runs in this order:

1. **Check-in** (`run-jq-signin.sh`) — a failure does not block the later steps. The check-in button is behind a
   jigsaw slider; with `JQ_SOLVE_CAPTCHA=1` (the unit sets it) `scripts/jq_auto_claim.py` solves it automatically.
   Set the flag to 0 in the unit environment to fall back to the manual flow (`ACTION REQUIRED` in the log).
2. **JQ sync** (`run-jq-sync.sh`) — CSI 1000/2000/300+500
3. **News / theme archive** (`run-news-archive.sh` → `scripts/news_archive.py`, `NEWS_ARCHIVE_ENABLE=true`) —
   a failure does not block the later steps. Writes `data/news/<source>/<YYYY-MM-DD>.jsonl`:
   - `ths_limit_up`: Tonghuashun limit-up pool with the editors' limit-up reason, seal times and board count;
     the site serves about one year back, so the first run backfills that year (about 6 minutes)
   - `em_zt_pool`: Eastmoney limit-up pool of the latest session (first-seal time, consecutive boards, industry)
   - `em_fast` / `sina_7x24`: 7x24 news flashes of the last `NEWS_ARCHIVE_LOOKBACK_HOURS` (default 30)
   - `irm_qa`: Shenzhen "Hudongyi" investor Q&A answered by the company, filed by answer time
   - `concept_tags`: Eastmoney concept tags of the universe, at most one snapshot a week
     (`data/news/concept_tags/<date>.csv.gz`), so that later studies can use the membership of the time
   None of these sources keeps a long history, which is why they are archived daily. Tagging the news with DeepSeek
   (`quant/ml/llm/`) is not implemented yet.
4. **Three-year LRU** (`run-retention-purge.sh`) — `DATA_RETENTION_YEARS=3` (configurable)
   - Delete rows with `date < cutoff` in tables such as `prices_daily` / `factors_snapshot` / `fundamentals_snapshot`
   - `VACUUM` reclaims disk space
   - Report: `output/home_data_node_retention_report.json`
5. **R2 cold backup** (optional, when `R2_ENABLE=true`), including `data/news/` as `news.tar.xz`

Credentials: `JQ_USERNAME` / `JQ_PASSWORD` in the repo-root `.env` (the same names as the GitHub Secrets and the
Gitee Go general variables).

The JoinQuant web check-in **can only run from a mainland-China IP**; it has been merged into `quant-daily` and no longer depends on GitHub Actions.

---

## 7. Code Hosting in China (GitHub-Compatible)

If GitHub access is unreliable, the recommended options are:

| Platform | URL | Parity with GitHub | Notes |
|------|------|-----------------|------|
| **Gitee** | gitee.com | ⭐⭐⭐⭐⭐ | PR/Issue/Actions (Gitee Go); can "Import from GitHub" |
| **GitCode** | gitcode.com | ⭐⭐⭐⭐ | CSDN/Huawei ecosystem, interface close to GitHub |
| **AtomGit** | atomgit.com | ⭐⭐⭐ | OpenAtom Foundation |

**Recommendation**: GitHub stays the main repo; Gitee (`git remote gitee` = `git@gitee.com:com0do/quant.git`, private) is a mirror
pushed together with GitHub. JoinQuant only serves Mainland-China IPs, so anything that logs in to JoinQuant cannot run on
GitHub-hosted runners: the daily check-in runs as the Gitee Go pipeline `.workflow/jq-signin.yml` (08:05 every day, entry
script `scripts/ci_jq_signin.sh`), with the home `quant-daily` job as the fallback. Its credentials `JQ_USERNAME` /
`JQ_PASSWORD` are Gitee Go general variables of the secret type, associated with the pipeline in its editor
(unassociated general variables are not injected), never in the repo; the pipeline file exports them with command
tracing off. The Gitee runner is not region-blocked; see `research/strong_dragon/README.md` §7 for the measured run
details.
Data sync still runs only on the home node.

---

## 8. FAQ

### Q: Can the machine be shut down?
**Yes**, but you have to accept the following trade-offs:
- Syncs skipped while it is off are **automatically caught up** after the next boot (systemd `Persistent=true`)
- But if it stays off for several days in a row, the sync window keeps growing and one day it may exceed JoinQuant's daily quota
- Electricity only costs ¥60~150/year, so leaving it on is better

If you must shut it down: **use Wake-on-LAN**. Enable WoL in the BIOS,
give it a fixed IP on the router, then have another machine run the following every morning:

```bash
wakeonlan aa:bb:cc:dd:ee:ff   # NIC MAC address of the HP 800G4
```

Or use RTC wake (`rtcwake -m off -t $(date -d "tomorrow 07:30" +%s)`)
so that the BIOS powers the machine on at the set time by itself.

### Q: What if the sync fails?

```bash
# View the logs
sudo journalctl -u quant-daily -n 200

# Common errors:
# - "missing JQ_USERNAME / JQ_PASSWORD" → top-level .env not configured properly
# - "Could not connect to (...:7000)"  → home network is down / blocked; switch networks
# - "最大查询限制" ("max query limit")  → JoinQuant daily quota used up; retries automatically tomorrow
# - "database is locked"               → another process is writing to SQLite; wait 5 minutes and rerun

# Rerun manually
sudo systemctl start quant-daily.service
```

### Q: How much R2 storage is used?

```bash
# Home machine
rclone size r2:quant-data/home/

# Output: Total objects: 42, Total size: 5.83 GiB (6259281920 Byte)
```

`R2_KEEP_LAST=14` × ~600 MB × 3 DBs × ~35% after compression = about **9 GB**,
which just fits within the 10 GB free tier. To keep more snapshots long term, reduce to `R2_KEEP_LAST=7`, or
upgrade to paid R2 ($0.015/GB/month; even 1 TB is only $15/month).

### Q: When should I consider upgrading to Postgres?

- The data volume reaches **10 GB+ with frequent concurrent reads** (multiple strategies running different backtests at the same time)
- Multiple machines need to write the same data at the same time (currently a single machine writes the R2 cold backup, which does not count as concurrent writes)
- You want to run complex SQL analysis (DuckDB is a lighter alternative that needs no extra service)

At the current scale (<2 GB of daily bars + a small amount of metadata), **SQLite is entirely enough; don't over-engineer**.

---

## 7. Relationship to Other Components

```
+---------------------------+
| JoinQuant JQData (thrift) |    ← reachable only from home
+-----------+---------------+
            | pull daily
            v
+------------------------+                     +---------------------+
| Home mini-PC           |  ---- push ---->    | Cloudflare R2       |
|   ~/quant/data/*.db    |                     |   quant-data/       |
|   systemd timer × 2    |                     |     home/YYYYMMDD/  |
|   (optional) Tailscale |                     |     home/latest/    |
+-----------+------------+                     +----------+----------+
            |                                             |
            | ssh + tailscale                             | https pull
            v                                             v
+--------------------------------------+       +---------------------+
| Interactive dev (home/remote/office) |       | GitHub Actions      |
+--------------------------------------+       |   backtest_check    |
                                               |   factor_analysis   |
                                               +---------------------+
                                                          |
                                                          v
                                               +------------------------------------------+
                                               | Windows QMT gateway                      |
                                               | (trading only, uses none of this data)   |
                                               +------------------------------------------+
```

---

## 8. Related Documents

- `deploy/home_data_node/README.md` — 30-second quick start
- `docs/05_live.md` — Windows QMT gateway deployment
- `docs/04_joinquant.md` — JoinQuant data schema and API
- `AGENTS.md` §5 data/config/secrets red lines

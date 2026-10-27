# deploy/home_data_node — Home Linux Data Node

**A single systemd timer job does everything** (a mainland-China IP is required):

| Step | What it does |
|------|------|
| 1 | JoinQuant web check-in to collect credits (slider captcha solved automatically; `JQ_SOLVE_CAPTCHA=0` to do it by hand) |
| 2 | Incremental JQ sync of CSI 1000 / 2000 / 300+500 |
| 3 | News / theme archive into `data/news/` (limit-up pools, news flashes, investor Q&A, concept tags) |
| 4 | **Three-year LRU**: delete expired SQLite rows + `VACUUM` |
| 5 | (Optional) R2 cold backup, including `data/news/` |

GitHub Actions does **not** do the check-in or the data sync (non-mainland-China IPs are blocked by JoinQuant).

Detailed documentation: [docs/09_home_data_node.md](../../docs/09_home_data_node.md)

## Quick Deploy

```bash
git clone git@github.com:com0do/quant.git ~/quant
cd ~/quant
cp .env.example .env && $EDITOR .env          # JQ_USERNAME / JQ_PASSWORD

cd deploy/home_data_node
cp .env.example .env && $EDITOR .env          # DAILY_ONCALENDAR / DATA_RETENTION_YEARS
sudo bash setup.sh

sudo systemctl start quant-daily.service        # Run it immediately to verify
sudo journalctl -u quant-daily -f
```

## Moving to a New Machine

```bash
tar czf quant.tgz -C ~/ quant --exclude='quant/.venv'
# After unpacking on the new machine:
cd ~/quant/deploy/home_data_node && sudo bash setup.sh
```

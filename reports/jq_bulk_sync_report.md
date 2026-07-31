# JQ Bulk Sync Report

- status: quota_blocked
- db: `data/market_cache.db`
- benchmark_index: `000852.XSHG`
- quota_exhausted: True
- quota_before: {'total': 1000000, 'spare': 0}
- quota_after: {}

## Progress Before
- {'db_path': 'data/market_cache.db', 'prices_daily_rows': 74700, 'prices_date_span': ('2024-12-16', '2025-12-23'), 'price_code_count': 300, 'sync_progress_total': 0, 'sync_progress_done': 0, 'sync_next_min': None, 'sync_next_max': None, 'snapshot_progress_total': 0, 'snapshot_progress_done': 0}

## Progress After
- {'db_path': 'data/market_cache.db', 'prices_daily_rows': 74700, 'prices_date_span': ('2024-12-16', '2025-12-23'), 'price_code_count': 300, 'sync_progress_total': 0, 'sync_progress_done': 0, 'sync_next_min': None, 'sync_next_max': None, 'snapshot_progress_total': 0, 'snapshot_progress_done': 0}

## Error
- Command '['uv', 'run', 'python', 'scripts/jq_bulk_sync_smallcap.py', '--index-code', '000852.XSHG', '--start-date', '2025-12-01', '--end-date', '2025-12-23', '--db-path', 'data/market_cache.db', '--sync-snapshots']' returned non-zero exit status 1.
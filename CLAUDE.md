# CLAUDE.md

PStock: FastAPI + web dashboard for technical analysis of Vietnamese stocks (HOSE/HNX/UPCOM).
User-facing docs are in `README.md` (Vietnamese). **Talk to the user in Vietnamese.**

## Commands (Windows, run from project root)

```bat
.venv\Scripts\python -m pytest -q                              :: tests (no network needed)
.venv\Scripts\python scripts\check_connection.py FPT --stream-seconds 30   :: SSI REST (6 TFs) + stream + Vietcap + Claude
.venv\Scripts\python -m uvicorn app.main:app --port 8000      :: dev server (run.bat does venv + install + start)
.venv\Scripts\python scripts\scan.py --timeframe 1D           :: CLI scanner
```
Always use `.venv\Scripts\python.exe`. On Linux the `accstock` bash script (repo root, LF, executable; renamed from
`pstock`) does `./accstock install` (.venv + requirements + `~/.local/bin/accstock` symlink) and `accstock start|stop|restart|status|logs`
(nohup uvicorn, PID in `data/pstock.pid` verified against `/proc/<pid>/cmdline`, log `data/logs/server.log`, exports
`TZ=Asia/Ho_Chi_Minh` because the app uses naive local time; default port 8000, `accstock start|restart <port>` overrides it and is saved in `data/pstock.port` so stop/status/logs
follow the running port; settings `PSTOCK_HOST/PORT` via env or `pstock.conf`; without `PSTOCK_HOST` the listen address is `APP_HOST` from `.env`,
like run.bat, else 127.0.0.1).
`.gitattributes` keeps `accstock`/`*.sh` LF and `*.bat` CRLF. It has only been syntax/lifecycle-tested under Git Bash,
not on a real Linux box yet. The console is cp1252: scripts that print Vietnamese must call
`sys.stdout.reconfigure(encoding="utf-8")`.

## Architecture

- `app/services/timeframes.py`: the timeframes (`5m 15m 30m 1h 1D 1W`, alias `60m`→`1h`), `resample`,
  VN trading sessions (09:00–11:30, 13:00–14:45), `future_times`, `fmt_time`, `chart_time`. Start here for
  anything time-related.
- `ssi_service.py`: REST fallback + the realtime stream, via the official `ssi-sdk` (API v3, `api.ssi.com.vn`). `SSI_CONSUMER_ID/SECRET` are
  passed as the SDK's `api_key/api_secret`. Market data and the stream need **no OTP**. The old v2
  `fc-data.ssi.com.vn` API rejects these keys ("This connection is invalid"), so don't go back to it.
- `realtime_service.py`: `Stream(auth)` / `StreamingService`, subscribes `subscribe_symbol_ohlcv(..., MINUTE_1)`
  for symbols opened on the chart (max 50). `connect()` runs its own daemon thread; `on_data` must stay short.
- `vietcap_service.py`: primary REST source (public `trading.vietcap.com.vn` gap-chart API, `ONE_MINUTE/ONE_HOUR/ONE_DAY`).
- `stock_service.py`: Vietcap → SSI per `DATA_SOURCE` (`auto` = Vietcap first | `vietcap` | `ssi` | `ssi_first`), CSV cache
  `data/cache/<SYM>_<TF>.csv` (REST + imported EOD rows, never stream data), then merges live stream bars (`_apply_live`).
- EOD import (`app/services/eod_import.py` `parse_eod` + `StockService.import_eod`, `POST /api/analysis/eod/import`, button
  "Import EOD" in the Phân tích tab): CafeF CSV/zip, thousand VND -> VND, only `^[A-Z0-9]{3}$` tickers (drops 8-char warrants/ETFs
  and indexes). Merged into `<SYM>_1D.csv`; each cache row keeps its own `source` (`cafef` = imported), the file is made stale so
  the next request only tops up the newest sessions (1D overlap 10 days, 1W 21). 1W of an imported symbol = `resample` of the 1D
  history (its 1W cache is deleted). On 1D/1W top-ups `adjust_ratio` compares overlapping closes: > 0.5% gap = re-adjusted
  history -> plain REST cache is reloaded in full; imported cache is kept and the symbol is flagged in `data/cache/eod_readjust.json`
  (source label "cần Import lại") until it is re-imported. Cache read-merge-write runs under `StockService._lock`, writes are atomic.
- `priceboard_service.py` + `sectors.py` + `app/api/board.py` (`GET /api/board`): the "Bảng điện" tab. Live
  quotes/indexes from Vietcap's public price board (`/api/price/v1/w/priceboard/tickers/price`, one POST for all
  ~290 symbols, ~2 s, cached 3 s; `marketIndex/getList`). Vietcap `va`/`totalValue` are in **million VND**
  (normalised to VND). The "Điểm" column is the 1D score, computed by one background thread through
  `StockService.get_ohlcv` and kept per day in `data/cache/board_scores.json`. Sectors: always read them via
  `sectors.get_sectors()` (the user's Excel import in `data/sectors.json`, gitignored, else `DEFAULT_SECTORS`);
  import/export = `parse_table` / `to_xlsx` (openpyxl), upload is the raw request body (no python-multipart).
  Import validates the file, then deletes every table (`reset_sectors` + `PriceBoardService.clear`) and saves the
  file's groups. Two pinned tables always come first (`split_pinned`, `board()` marks them `pinned`): "Quan Tâm" =
  the browser's watchlist (localStorage `pstock.watch`, sent as `?watch=`; a "Quan Tâm" group in an imported file is
  returned as `watch`, not stored) and "VN30" = the list's own VN30 group, else the live basket. Export includes both.
  `tests/conftest.py` redirects `sectors_path` to tmp for every test: tests must never touch the user's file. `POST /api/board/telegram`
  sends strong/weak movers (`change_pct` >= +threshold / <= −threshold, untraded ignored) as fixed-width `<pre>`
  tables built by `app/analysis/board_alerts.py` (layout pinned by `test_row_matches_the_template_layout`; long lists
  are split into several messages, each with whole `<pre>` blocks). %V20p uses `vol_avg20` from the score worker.
- Scanner strength (`app/analysis/strength.py`, `GET /api/board/strength`): computed by the same board background worker
  (`_score_worker` stores `strength_metrics` per symbol; an entry is done when it has a `strength` key) plus VNINDEX
  history fetched once per run. `rank_universe` does everything that needs the whole universe: RS Rating percentile,
  the RS ≥ 80 / ≥ 70 conditions, sector strength (mean RS Rating, industry sectors only), composite 40/40/20.
- ACC Ranking (`app/analysis/acc_strength.py`, `GET /api/board/acc`, tab "Xếp hạng ACC" `#acc`): spec in
  `../Requirements/Stock_Strength_Monitor_V1.md`. Same worker: `entry["acc"] = acc_pair(completed(df), VN30)` = metrics for the
  last completed session and the one before (Δ / rank change). `completed()` drops today's bar before 15:00. `_done` needs both
  `strength` and `acc` keys. `rank_acc` does the cross-sectional parts (RS percentiles vs VN30, ROC20 percentile, ATR% top 20%),
  Swing/Position/Combined, Setup, Risk, signal and the market overview. Its own Ichimoku is 9-17-26 (disp. 26), not the app preset.
  Points tables (`TREND`, `MOMENTUM`, ...) feed the scores and the dashboard legend; each group must total 100 (tested).
- Wyckoff × Ichimoku (`app/analysis/wyckoff.py`, `GET /api/analysis/{symbol}/wyckoff`, tab "Phân tích" `#analysis`): one 1D fetch,
  weekly = `resample`. `find_tr` (first held breakout in the last `recent` bars, else the TR still running; `HOLD_BARS`
  rejects "breakouts" inside a wider TR), `pnf_columns` (3-box reversal), Count = columns × box × reversal, T1/T2/T3 = bottom/mid/top
  (down: top/mid/bottom) ± Count; `ichimoku_levels` (1D + 1W: Kijun, Kijun 65/129, cloud now / +26, twists) → `confluence`
  (zones, obstacles, scenarios A/B/C/D, weights in `ichimoku_levels`). Manual TR via `start`/`end`. Long TRs give very far targets
  (Count scales with the number of swings) — `warnings` flags it. The chart gets the whole history (`MAX_CHART_BARS`) and
  `view_bars` = initial zoom (recent bars + TR); the browser uses `setVisibleLogicalRange`, the user scrolls back for older data. Tests: `tests/test_wyckoff.py` (synthetic base + breakout).
- `app/analysis/`: Ichimoku Trịnh Phát (9-17-26-33 + Kijun 65/129, time cycles), MACD, RSI, breakout;
  `stock_scanner.py` holds scoring, `analyze_dataframe`, `chart_payload`.
- Custom Ichimoku (Cổ phiếu tab only): `GET /api/analysis/{sym}` and `/chart` take optional `tenkan kijun senkou_a
  senkou_b chikou` (`ichi_params` → `ichimoku.custom_params`; `senkou_a` = cloud shift = `displacement`, `chikou` =
  `IchiParams.lag`, None = displacement). `enrich/score_series/analyze_dataframe/chart_payload` take `p`. The browser keeps
  the changed fields in localStorage `pstock.ichi`; "IchiTP Mặc Định" clears it. Board, scanner, Telegram, ACC stay on
  the app setup. The Kumo fill is a lightweight-charts custom series (`KumoView`) over `{time, a, b}`.
- `app/templates/dashboard.html`: single page, lightweight-charts **4.2.0** bundled offline in `app/static/js`.
  Bảng điện is the homepage (`/`; `#stock`, `#scan`). The global header (index cards + colour legend) is on every tab:
  `drawIndexes`, fed by the board poll on Bảng điện, else by `loadIndexes` (`GET /api/board/indexes`). Each card is built
  once (`idxCard`) with a mini lightweight-charts chart (baseline series around the reference + volume area), fed by
  `loadIndexCharts` (`GET /api/board/index-chart?interval=1|3`, `build_index_chart`). Vietcap's gap-chart returns
  nothing when several index symbols are asked at once: one request per index (`_minute_bars`), run in parallel. The symbol box and
  timeframes live in the Cổ phiếu tab; its chart loads on first visit (`STOCK_LOADED`, `openStock(sym)`).
- Session day (`priceboard_service.board_day` / `is_live`): the board, index cards and header charts show the last
  session until 08:56 of the next weekday. Outside the live window (weekday < 08:56, weekends) `quotes()`/`indexes()`
  serve the snapshot saved while live (`data/cache/board_last_session.json`, written <= once a minute; conftest
  redirects it to tmp). From 08:56 the charts show the new day: `session_axis` = 09:00..14:45 so the axis starts at
  09:00, no bars until the first one, ref = previous session's last close (the live index ref only once bars exist).
  `chart_day`: still no VN-Index bar of today at 09:15 = holiday, keep the last session. The reference is a dashed
  yellow line series over the whole axis (`refSeries`), so it is always inside the autoscale.

## Invariants: don't break these

- **One SSI session per API key.** `SSIService` owns the only `Auth`; `RealtimeService` reuses it. Never create a
  second `Auth` with the same key in the app. Running a script while the server is up can take over the
  server's session (the server re-authenticates on `AuthenticationError`).
- **Times are naive Vietnam-local datetimes** everywhere in DataFrames. Intraday bars are labelled by start
  time. For the chart, intraday time = VN wall-clock encoded as UTC epoch seconds; 1D/1W = `'YYYY-MM-DD'`.
- **SSI has no 30m or 1W**: 30m is resampled from 15m and 1W from 1D (week labelled by its Monday). SSI intraday
  history only goes back ~7 months; 1D goes back to 2018.
- **`score_signals` (latest bar) and `score_series` (every bar, vectorised) must give identical scores.**
  Change both together; `test_score_series_matches_point_in_time_score` enforces it. Score is clipped to ±100
  (raw range ±110). Rating bands: ≥60, ≥25, >−25, >−60.
- **Live merge**: intraday buckets are replaced by stream data only if they started at or after the first
  streamed minute; for 1D/1W only high/low/close of the current bar are updated (volume stays REST).
- **Dashboard number format**: prices via `px()` = thousand VND with 2 decimals (`50400` → `50.40`), applied to
  every price-scale series via `PRICE_FMT`; every other number via `fmt()` = `en-US` (dot decimal, comma
  thousands). MACD stays in VND. Data and the API always stay in VND; only display is scaled.
- lightweight-charts `series.update()` throws for a time older than the last bar: live polling only pushes
  bars with `time >= lastT`.
- `app/__init__.py` injects `truststore` (Windows cert store) because the corporate proxy re-signs TLS. Anything
  outside the `app` package that makes HTTPS calls must import `app` first.

## Daily Telegram alert

- `scripts/daily_signals.py` (via `daily_signals.bat`, Windows Task Scheduler task "PStock Daily Signals", 15:05
  Mon–Fri, StartWhenAvailable). Logic in `app/analysis/daily_signals.py`; sending in `telegram_service.py`.
- Symbols = every Bảng điện symbol (`daily_signals.board_symbols` = `PriceBoardService.universe(watch)`: VN30 +
  sector tables + Quan Tâm, deduped); `daily_signals.scan` fetches them with 4 threads (~270 symbols, ~20 s). Both BO
  and BD sections are sorted by score high -> low. The "Gửi BO/BD" button (`POST /api/board/daily`) does the same scan
  with the app's services (cut at the previous trading day before 15:05). `VietcapService` keeps one pooled
  `httpx.Client`: a new connection per request made the scan ~10x slower behind the proxy, and threads didn't help.
- BO/BD = Ichimoku cloud break on the latest bar (`cloud_breakout`/`cloud_breakdown` columns from
  `compute_ichimoku`); the 20-bar price/volume BO/BD is listed as a secondary section. Signals don't change the score.
- Skips sending when no symbol has a bar dated today (holiday, or the laptop woke up the next day), and only
  reports symbols whose last bar is today's.
- Secrets live in `telegram.env` (gitignored, loaded as a second env file by `Settings`). Never print the
  bot URL: it contains the token (`TelegramService._call` keeps it out of error messages).
- With the default `DATA_SOURCE=auto` the job reads Vietcap and only logs in to SSI when Vietcap fails. If it
  does, it creates its own SSI `Auth`, so if the server is running at 15:05 it takes over the session once;
  the server re-authenticates on its next request.

## Intraday BO bulletin (Cổ phiếu tab)

- `app/analysis/intraday_bo.py` + `app/services/bo_scheduler.py` (started in the lifespan next to the board scheduler),
  `POST /api/board/bo {timeframe: 30m|1h, dry_run}`. Every board symbol (`board_symbols`), Ichimoku BO only (no BD) on
  the latest **completed** 30m/60m bar (`completed` drops the bar in progress; bar end is capped at 11:30 / 14:45).
  Slots = bar end + 5 min, the 14:45 bar at 15:05: 30m 09:35 ... 11:35, 13:35 ... 15:05; 60m 10:05 11:05 11:35 14:05
  15:05. Only symbols whose last completed bar is the slot's bar are reported; Telegram only gets a message when there
  is at least one BO (manual button too). Settings `auto_bo` (default off) / `bo_tf` (`30m`|`1h`) in
  board_settings.json; last handled slot in `data/bo_alert_state.json`; slots missed by > 20 min are skipped.
  The Cổ phiếu tab defaults to 60m. A full 30m scan needs the 1-minute history of ~270 symbols: ~100 s the first
  time, ~20 s once cached (60m ~20 s).

## Telegram bot command /ck

- `app/services/telegram_bot.py` (lifespan task, long polling `getUpdates` 10 s) + `app/analysis/stock_brief.py`
  (`parse_command`, `build_ck_message`). `/ck FPT` -> 1D `analyze_dataframe` + the live board quote; `/help`, `/start`.
  Only chats in TELEGRAM_CHAT_ID are answered; messages older than 5 min at startup are skipped; `TELEGRAM_COMMANDS=false`
  turns it off. One getUpdates consumer per token: the same token in two running apps (Windows + Linux) gives
  409 Conflict and only one answers. Telegram HTML: escape `<`/`>` (tested by `test_ck_message_is_valid_telegram_html`).

## Scheduled board bulletin

- `app/services/board_scheduler.py` polls the settings during FastAPI lifespan and sends the board bulletin on weekdays
  inside `board_start`–`board_end`, at `board_interval` minutes. It also checks `PriceBoardService.trading_today`
  (no weekend or holiday session), and persists the last sent slot in `data/board_alert_state.json`. The app must be
  running. The old "PStock Board Alerts" Task Scheduler trigger is obsolete; `scripts/board_alerts.py` without
  `--once` / `--force` is a no-op. The message is the same as `POST /api/board/telegram`:
  `build_movers_message(..., watch=)` adds "3. Quan Tam (A-Z)". Telegram can't colour text (and shows `<pre>` in the
  app's own monospace colour): rows end with a marker (`board_alerts.kind`/`mark`: 🩷 > +6.5%, 🟢, 🟡, 🔴, 🔵 < −6.5%,
  ⚪ untraded). Sending as images was tried and rolled back at the user's request (the group also blocks bot photos:
  "not enough rights to send photos"). Escape anything with `<`/`>` in the message.
- The Quan Tâm list, threshold, time window and interval live in `data/board_settings.json`
  (`board_settings.py`, `GET/PUT /api/board/settings`);
  the dashboard writes it on every change (`setWatch`, `syncSettings` on load: server wins, a browser-only list is
  uploaded). `conftest.py` redirects it to tmp too. It also holds the auto-send switches `auto_board` / `auto_daily`
  (default on; `auto_board` controls the app scheduler, `auto_daily` controls the daily script). Without the server:
  `scripts/auto_send.py [on|off] [board|daily]`
  (`auto_send.bat`, Linux `accstock auto`). Scripts call `sys.stdout.reconfigure` before argparse (Vietnamese `--help`).
- Manual `scripts/board_alerts.py --once` runs without the server (`PriceBoardService(stock=None)`): vol_avg20 from
  today's `board_scores.json`, else `fill_vol_avg_from_cache` (1D CSVs, no network).
- `.bat` files must be CRLF (with LF, cmd misreads lines around the UTF-8 comments).

## Not yet verified

The stream has only been tested outside trading hours (connect + subscribe OK, no bars received). Still
unconfirmed: the real format of `IntervalMessage.interval_time` (`parse_bar_time` accepts epoch, ISO,
`YYYY/MM/DD HH:MM:SS`, `HH:MM:SS`) and whether `volume` is per-minute or cumulative. Check with
`check_connection.py` during a session before relying on live volume.

## Tests

`tests/test_services_and_api.py` overrides `get_stock_service` with `FakeStock`. When the `StockService` interface
changes (new kwargs, `realtime`, `live_bars`), update `FakeStock`/`FakeRealtime` too.

## Git

Remote `origin` = `https://bbk-rndsoft.ingenico.com/scm/~thngo/accstock.git`, branch `main` (fresh history since 2026-11-10; the old `pstock.git` / `master` is no longer used). `.env` (real SSI keys),
`telegram.env` (bot token) and `data/cache/*.csv` are gitignored; never commit them. Commit/push only when asked.

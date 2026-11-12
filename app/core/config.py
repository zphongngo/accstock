"""Application settings loaded from environment variables / .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # telegram.env holds the bot token / chat ID (kept apart from .env, both gitignored)
        env_file=(str(BASE_DIR / ".env"), str(BASE_DIR / "telegram.env")), env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    APP_NAME: str = "PStock"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_API_KEY: str = ""  # optional: if set, /api/* requires header X-API-Key

    # --- SSI (ssi-sdk, API v3) ---
    # SSI_CONSUMER_ID / SSI_CONSUMER_SECRET are passed to the SDK as api_key / api_secret.
    SSI_CONSUMER_ID: str = ""
    SSI_CONSUMER_SECRET: str = ""
    SSI_CLIENT_ID: str = ""    # optional
    SSI_PRIVATE_KEY: str = ""  # only needed for trading (orders), not for market data
    SSI_API_URL: str = "https://api.ssi.com.vn"
    SSI_STREAMING_URL: str = "wss://stream.ssi.com.vn/ws/v3"
    SSI_RATE_LIMIT_PER_SECOND: int = 10
    SSI_STREAM_ENABLED: bool = True  # realtime 1-minute bars over WebSocket (StreamingService)
    SSI_LOG_LEVEL: str = "WARNING"

    # --- Vietcap ---
    VIETCAP_URL: str = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"

    # --- Data ---
    DATA_SOURCE: str = "auto"  # auto (Vietcap -> SSI) | vietcap | ssi | ssi_first (SSI -> Vietcap)
    DATA_DIR: Path = BASE_DIR / "data"
    CACHE_TTL_MINUTES: int = 30  # 1D / 1W; intraday timeframes use shorter TTLs (timeframes.py)
    HISTORY_DAYS: int = 500  # 1D history in calendar days (~340 phiên, đủ cho Kijun 129 và chu kỳ 257)

    # --- Ichimoku Trịnh Phát ---
    ICHI_PRESET: str = "trinhphat"  # trinhphat (9-17-26-33 + Kijun 65/129) | classic (9-26-26-52 + 129) | custom
    ICHI_TENKAN: int = 9            # chỉ dùng khi ICHI_PRESET=custom
    ICHI_KIJUN: int = 17
    ICHI_SENKOU_B: int = 33
    ICHI_DISPLACEMENT: int = 26
    ICHI_LONG_KIJUN: str = "65,129"
    ICHI_TIME_CYCLES: str = "9,17,26,33,42,65,76,129,172,200,257"
    ICHI_CYCLE_TOLERANCE: int = 1
    ICHI_PIVOT_LOOKBACK: int = 129

    # --- Claude (Anthropic) ---
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_MAX_TOKENS: int = 1500

    # --- Telegram (daily BO/BD alert, scripts/daily_signals.py) ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""   # one or more chat IDs, comma-separated
    TELEGRAM_SEND_EMPTY: bool = True  # also send a message when no symbol has a signal
    TELEGRAM_COMMANDS: bool = True    # answer /ck <symbol> in the TELEGRAM_CHAT_ID chats while the app runs

    # --- Scanner ---
    WATCHLIST: str = (
        "ACB,BCM,BID,BVH,CTG,FPT,GAS,GVR,HDB,HPG,LPB,MBB,MSN,MWG,PLX,"
        "SAB,SHB,SSB,SSI,STB,TCB,TPB,VCB,VHM,VIB,VIC,VJC,VNM,VPB,VRE"
    )

    @property
    def watchlist(self) -> list[str]:
        return [s.strip().upper() for s in self.WATCHLIST.split(",") if s.strip()]

    @property
    def cache_dir(self) -> Path:
        p = Path(self.DATA_DIR) / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()

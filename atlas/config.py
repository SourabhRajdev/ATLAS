"""Configuration — single source of truth for all settings.

Secrets are pulled from macOS Keychain at startup, not from .env files.
.env is supported only for non-mac (CI) environments.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas.core.secrets import get_secret


def _keychain_default(name: str) -> str:
    """Used as Pydantic field default — pulls from Keychain at construction."""
    return get_secret(name) or ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",  # CI only; mac uses Keychain
        env_file_encoding="utf-8",
    )

    # LLM — Keychain first, env fallback inside get_secret()
    gemini_api_key: str = ""
    model: str = "gemini-2.5-flash"
    max_tokens: int = 8192
    max_tool_rounds: int = 15  # safety: prevent infinite tool loops
    
    # Voice (optional)
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""

    # Multi-provider failover
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Local LLM (Ollama)
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"

    # Web search
    serper_api_key: str = ""

    # GitHub
    github_token: str = ""

    # Paths
    data_dir: Path = Path.home() / ".atlas"

    # Cost tracking (per million tokens, USD)
    input_cost_per_mtok: float = 0.0    # Gemini 2.0 Flash is free during preview
    output_cost_per_mtok: float = 0.0   # Gemini 2.0 Flash is free during preview

    # Behavior
    log_level: str = "INFO"
    confirm_destructive: bool = True
    
    # Autonomy
    autonomy_enabled: bool = True
    autonomy_interval: int = 30  # seconds between autonomy cycles
    default_mode: str = "assistive"  # passive | assistive | autonomous
    
    # Voice
    voice_mode: str = "push_to_talk"  # push_to_talk | continuous

    @property
    def db_path(self) -> Path:
        return self.data_dir / "atlas.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "atlas.log"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def model_post_init(self, __context) -> None:
        """Hydrate empty secrets from Keychain."""
        if not self.gemini_api_key:
            self.gemini_api_key = get_secret("GEMINI_API_KEY") or ""
        if not self.deepgram_api_key:
            self.deepgram_api_key = get_secret("DEEPGRAM_API_KEY") or ""
        if not self.elevenlabs_api_key:
            self.elevenlabs_api_key = get_secret("ELEVENLABS_API_KEY") or ""
        if not self.groq_api_key:
            self.groq_api_key = get_secret("GROQ_API_KEY") or ""
        if not self.serper_api_key:
            self.serper_api_key = get_secret("SERPER_API_KEY") or ""
        if not self.github_token:
            self.github_token = get_secret("GITHUB_TOKEN") or ""

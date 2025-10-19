"""
Configuration management for IPFS Storage System.

Handles environment variables and application settings.
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
    )

    # Server Configuration
    host: str = Field(default_factory=lambda: os.getenv("IPFS_HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.getenv("IPFS_PORT", "8003"))
    )
    debug: bool = Field(default_factory=lambda: (os.getenv("IPFS_DEBUG", "false")).lower() == "true")

    # IPFS Configuration
    ipfs_api_url: str = Field(
        default_factory=lambda: os.getenv("IPFS_API_URL", "http://127.0.0.1:5001")
    )
    ipfs_gateway_url: str = Field(
        default_factory=lambda: os.getenv("MODSDK_IPFS_GATEWAY_URL")
        or os.getenv("IPFS_GATEWAY_URL", "http://127.0.0.1:8080")
    )
    ipfs_timeout: int = Field(
        default=int(
            os.getenv("MODSDK_IPFS_TIMEOUT")
            or os.getenv("IPFS_TIMEOUT", 30)
        )
    )
    ipfs_repo_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("MODSDK_IPFS_REPO_PATH")
            or os.getenv("IPFS_REPO_PATH", "")
        )
    )
    ipfs_api_port: int = Field(
        default_factory=lambda: int(
            os.getenv("MODSDK_IPFS_API_PORT")
            or os.getenv("IPFS_API_PORT", "5001")
        )
    )
    ipfs_gateway_port: int = Field(
        default_factory=lambda: int(
            os.getenv("MODSDK_IPFS_GATEWAY_PORT")
            or os.getenv("IPFS_GATEWAY_PORT", "8080")
        )
    )
    ipfs_swarm_port: int = Field(
        default_factory=lambda: int(
            os.getenv("MODSDK_IPFS_SWARM_PORT")
            or os.getenv("IPFS_SWARM_PORT", "4001")
        )
    )

    # Database Configuration
    database_url: str = Field(default=os.getenv("IPFS_DATABASE_URL", "sqlite:///./files.db"))

    # Security Configuration (Optional)
    secret_key: str | None = Field(default=os.getenv("IPFS_SECRET_KEY"))
    algorithm: str = Field(default=os.getenv("IPFS_ALGORITHM", "HS256"))
    access_token_expire_minutes: int = Field(default=int(os.getenv("IPFS_ACCESS_TOKEN_EXPIRE_MINUTES", 30)))

    # File Upload Configuration
    max_file_size: int = Field(default=int(os.getenv("IPFS_MAX_FILE_SIZE", 100 * 1024 * 1024)))  # 100MB
    allowed_extensions: str = Field(
        default=os.getenv("IPFS_ALLOWED_EXTENSIONS", "txt,pdf,png,jpg,jpeg,gif,mp4,mp3,doc,docx,zip,tar,gz,py,rs,js,ts")
    )

    # Application Configuration
    app_name: str = Field(default=os.getenv("IPFS_APP_NAME", "IPFS Storage System"))
    version: str = Field(default=os.getenv("IPFS_VERSION", "0.1.0"))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="IPFS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @property
    def allowed_extensions_list(self) -> list[str]:
        """Get allowed file extensions as a list."""
        return [ext.strip().lower() for ext in self.allowed_extensions.split(",") if ext.strip()]

    @property
    def resolved_repo_path(self) -> Path:
        """Resolve the IPFS repository path with a sensible default."""
        if str(self.ipfs_repo_path):
            return Path(self.ipfs_repo_path).expanduser().resolve()
        return (self.project_root / ".ipfs").resolve()


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()

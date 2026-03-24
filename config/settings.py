import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    """Loads and validates all configuration from environment variables."""

    # KIS API credentials
    APP_KEY: str = os.getenv("KIS_APP_KEY", "")
    APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
    ACCOUNT_NUMBER: str = os.getenv("KIS_ACCOUNT_NUMBER", "")

    # KIS API base URLs
    REAL_DOMAIN: str = "https://openapi.koreainvestment.com:9443"
    MOCK_DOMAIN: str = "https://openapivts.koreainvestment.com:29443"

    # Toggle real vs. paper-trading environment
    IS_MOCK: bool = os.getenv("KIS_IS_MOCK", "true").lower() == "true"

    @classmethod
    def get_base_url(cls) -> str:
        """Return the appropriate base URL based on trading mode."""
        return cls.MOCK_DOMAIN if cls.IS_MOCK else cls.REAL_DOMAIN

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError if any required credential is missing."""
        missing = [
            name
            for name, value in {
                "KIS_APP_KEY": cls.APP_KEY,
                "KIS_APP_SECRET": cls.APP_SECRET,
                "KIS_ACCOUNT_NUMBER": cls.ACCOUNT_NUMBER,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

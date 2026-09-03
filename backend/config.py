import os
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv(dotenv_path=".env", override=True)

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", extra="ignore")
        
        PROJECT_NAME: str = "FORGE Autonomous CTF Framework"
        VERSION: str = "1.0.0"
        ENVIRONMENT: str = "development"
        HOST: str = "127.0.0.1"
        PORT: int = 8000
        SECRET_KEY: str = "forge-secret-key-change-in-production"
        
        # Database
        DATABASE_URL: str = "sqlite:///./forge.db"
        
        # API Keys
        GEMINI_API_KEY: str = ""
        NVIDIA_API_KEY: str = ""
        CEREBRAS_API_KEY: str = ""
        OPENROUTER_API_KEY: str = ""
        HF_TOKEN: str = ""
        CLOUDFLARE_API_TOKEN: str = ""
        CLOUDFLARE_ACCOUNT_ID: str = ""
        AGENTROUTER_API_KEY: str = ""
        MISTRAL_API_KEY: str = ""
        COHERE_API_KEY: str = ""
        GROQ_API_KEY: str = ""
        
        # Budget Controls
        PAID_MODEL_ALLOWED: bool = True
        DAILY_BUDGET_USD: float = 5.00
        SESSION_BUDGET_USD: float = 2.00

except ImportError:
    class Settings:
        def __init__(self):
            self.PROJECT_NAME = os.getenv("PROJECT_NAME", "FORGE Autonomous CTF Framework")
            self.VERSION = os.getenv("VERSION", "1.0.0")
            self.ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
            self.HOST = os.getenv("HOST", "127.0.0.1")
            self.PORT = int(os.getenv("PORT", 8000))
            self.SECRET_KEY = os.getenv("SECRET_KEY", "forge-secret-key")
            self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./forge.db")
            self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
            self.NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
            self.CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
            self.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
            self.HF_TOKEN = os.getenv("HF_TOKEN", "")
            self.CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
            self.CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
            self.AGENTROUTER_API_KEY = os.getenv("AGENTROUTER_API_KEY", "")
            self.MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
            self.COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
            self.GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
            self.PAID_MODEL_ALLOWED = os.getenv("PAID_MODEL_ALLOWED", "true").lower() == "true"
            self.DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", 5.0))
            self.SESSION_BUDGET_USD = float(os.getenv("SESSION_BUDGET_USD", 2.0))

settings = Settings()

import os
try:
    from pydantic_settings import BaseSettings
except ImportError:
    try:
        from pydantic import BaseSettings
    except ImportError:
        class BaseSettings:
            def __init__(self, **kwargs):
                for k, v in self.__class__.__dict__.items():
                    if not k.startswith("_") and not callable(v):
                        setattr(self, k, os.environ.get(k, v))

class Settings(BaseSettings):
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
    PAID_MODEL_ALLOWED: bool = False
    DAILY_BUDGET_USD: float = 5.00
    SESSION_BUDGET_USD: float = 2.00

settings = Settings()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ai_api_key: str  # required: the app fails fast at boot if no AI_API_KEY is set
    github_token: str | None = None
    ai_model: str = "claude-haiku-4-5"
    max_repos: int = 25
    readme_max_chars: int = 12000  # README is sliced by character count before the prompt
    concurrency: int = 6

    model_config = {"env_file": ".env"}


settings = Settings()

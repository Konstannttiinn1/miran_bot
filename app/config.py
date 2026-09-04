from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки бота. Читаются из файла .env в корне проекта."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    bot_token: str = ""
    admin_ids: str = ""
    support_username: str = "@support"

    # База данных
    db_url: str = "sqlite+aiosqlite:///vpn.db"

    # Панель 3x-UI
    xui_host: str = ""
    xui_base_path: str = ""
    xui_username: str = ""
    xui_password: str = ""
    xui_token: str = ""
    xui_inbound_id: int = 1
    xui_sub_url: str = ""

    # Heleket
    heleket_merchant_id: str = ""
    heleket_api_key: str = ""
    heleket_webhook_url: str = ""
    heleket_enabled: bool = False

    # Локальные цены / Telegram Stars
    rub_per_usd: float = 90.0
    toman_per_usd: float = 61000.0
    stars_reward_usd: float = 0.013

    # Дилер: внутренний баланс всегда в USD
    dealer_discount: float = 0.5
    dealer_card_number: str = "0000-0000-0000-0000"
    dealer_contact: str = "@dealer"

    # Языки и оформление
    available_langs: str = "fa,en,ru"
    use_custom_emoji: bool = False

    @property
    def admin_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    @property
    def langs_list(self) -> list[str]:
        return [x.strip() for x in self.available_langs.split(",") if x.strip()]


settings = Settings()

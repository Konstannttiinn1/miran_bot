from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки бота. Читаются из файла .env в корне проекта."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    bot_token: str = ""
    admin_ids: str = ""  # формат: "111,222"
    support_username: str = "@support"

    # База данных (локально sqlite, на сервере postgres)
    db_url: str = "sqlite+aiosqlite:///vpn.db"

    # Панель 3x-UI
    xui_host: str = ""        # https://83.243.86.111:27320
    xui_base_path: str = ""   # /bIwwjwThZ7fpVh9dji/
    xui_username: str = ""
    xui_password: str = ""
    xui_token: str = ""       # API-токен (основной способ)
    xui_inbound_id: int = 1
    xui_sub_url: str = ""     # база ссылки подписки из настроек панели

    # Heleket
    heleket_api_key: str = ""
    heleket_webhook_url: str = ""
    heleket_enabled: bool = False  # включишь, когда получишь ключ

    # Дилер
    dealer_card_number: str = "0000-0000-0000-0000"
    dealer_contact: str = "@dealer"

    # Языки и оформление
    available_langs: str = "fa,en,ru"
    use_custom_emoji: bool = False

    @property
    def admin_list(self) -> list[int]:
        """Список Telegram ID супер-админов."""
        return [int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    @property
    def langs_list(self) -> list[str]:
        """Список доступных языков для клиентов."""
        return [x.strip() for x in self.available_langs.split(",") if x.strip()]


settings = Settings()
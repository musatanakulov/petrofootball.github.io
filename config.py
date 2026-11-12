"""Настройки из переменных окружения и файла .env (без внешних зависимостей)."""
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


_load_env_file(os.path.join(_BASE_DIR, ".env"))

# Токен бота от @BotFather. Нужен и боту, и серверу (для проверки подписи Telegram initData).
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Telegram ID администраторов через запятую: ADMIN_TELEGRAM_IDS=111111,222222
ADMIN_TELEGRAM_IDS = {
    int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(";", ",").split(",")
    if x.strip().isdigit()
}

# 1 = админ без Telegram, но только для запросов с этого же компьютера (localhost без прокси).
# Запросы из интернета (в т.ч. через туннель ngrok/Cloudflare) всё равно требуют подписи Telegram.
ALLOW_DEV_ADMIN = os.getenv("ALLOW_DEV_ADMIN", "0").strip() == "1"

# Часовой пояс турнира (смещение от UTC в часах). Петропавловск, Казахстан: UTC+5.
# Голосование идёт с понедельника 00:00 до вторника 00:00 по этому времени.
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "5"))

# Сколько секунд действительна подпись initData (по умолчанию 24 часа)
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))

# Адрес Mini App для кнопки бота
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://musatanakulov.github.io/petrofootball.github.io/")

# Домены, которым разрешено обращаться к API из браузера (через запятую). По умолчанию — любые.
# На сервере: CORS_ORIGINS=https://petrofootball.kz
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()] or ["*"]

# 1 = открыть страницу документации API (/docs). На публичном сервере не включать.
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0").strip() == "1"

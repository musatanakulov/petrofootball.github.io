import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# Вставьте сюда токен, который вы получили от @BotFather
TOKEN = "9109248559:AAHPiDZyDRlBO2oItBBK64AZFIOrkc9M-TM"

# Ссылка на ваш опубликованный фронтенд на GitHub Pages
WEBAPP_URL = "https://musatanakulov.github.io/petrofootball.github.io/"

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Создаем инлайн-кнопку с открытием Mini App
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏆 Открыть турнир и лиги",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]
        ]
    )
    
    await message.answer(
        f"Привет, <b>{message.from_user.first_name}</b>! 👋\n\n"
        "Добро пожаловать в официальный менеджер футбольного турнира.\n"
        "Нажмите кнопку ниже, чтобы открыть таблицы лиг, профили команд и статистику игроков прямо в Telegram:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def main():
    # Запуск процесса поллинга новых апдейтов
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("Бот успешно запущен и ожидает сообщений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
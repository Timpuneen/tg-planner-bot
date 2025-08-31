import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/telegram_bot")

# Admin settings
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))  # Добавь свой Telegram ID в .env

# Encryption settings
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")  # Добавь в .env файл

# Popular timezones
POPULAR_TIMEZONES = [
    ("UTC", "UTC"),
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Kiev", "Киев (UTC+2)"),
    ("Asia/Almaty", "Алматы (UTC+5)"),
    ("Europe/London", "Лондон (UTC+0)"),
    ("Europe/Berlin", "Берлин (UTC+1)"),
    ("America/New_York", "Нью-Йорк (UTC-5)"),
    ("America/Los_Angeles", "Лос-Анджелес (UTC-8)"),
    ("Asia/Tokyo", "Токио (UTC+9)"),
    ("Australia/Sydney", "Сидней (UTC+10)")
]
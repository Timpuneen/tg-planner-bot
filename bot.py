import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.connection import db
from handlers import start, menu, reminders, tasks, diary
from services.scheduler_service import init_scheduler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

async def main():
    """Основная функция запуска бота"""
    try:
        # Подключение к базе данных
        await db.connect()
        
        # Инициализация планировщика
        scheduler = init_scheduler(bot)
        await scheduler.start()
        
        # Регистрация обработчиков
        dp.include_router(start.router)
        dp.include_router(menu.router)
        dp.include_router(reminders.router)
        dp.include_router(tasks.router)
        dp.include_router(diary.router)
        
        # Запуск бота
        logger.info("Bot started")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
    finally:
        # Остановка планировщика
        if 'scheduler' in locals():
            await scheduler.stop()
        # Закрытие соединений
        await db.disconnect()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
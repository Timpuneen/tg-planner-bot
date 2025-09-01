import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from config import BOT_TOKEN
from database.connection import db
from handlers import start, menu, reminders, tasks, diary, admin
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

async def health_check(request):
    """Keepalive endpoint для Railway"""
    print(f"Health check at {request.path}")
    return web.Response(text="OK", status=200)

async def stats_endpoint(request):
    """Дополнительный endpoint с простой статистикой"""
    print(f"Stats check at {request.path}")
    try:
        async with db.pool.acquire() as conn:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            
            tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")

            reminders_count = await conn.fetchval("SELECT COUNT(*) FROM reminders")
            active_reminders = await conn.fetchval("SELECT COUNT(*) FROM reminders WHERE is_active = TRUE")
            
            diary_entries_count = await conn.fetchval("SELECT COUNT(*) FROM diary_entries")
        
        stats = {
            "status": "running",
            "users": users_count,
            "tasks_count": tasks_count,
            "reminders_count": reminders_count,
            "active_reminders": active_reminders,
            "diary_entries_count": diary_entries_count,
            "scheduler": "active"
        }
        return web.json_response(stats)
    except Exception as e:
        logger.error(f"Error in stats endpoint: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def start_web_server():
    """Запуск HTTP сервера для keepalive"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/stats', stats_endpoint)
    
    # Railway предоставляет переменную PORT
    port = int(os.getenv('PORT', 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Web server started on port {port}")
    return runner

async def main():
    """Основная функция запуска бота"""
    web_runner = None
    scheduler = None
    
    try:
        # Подключение к базе данных
        await db.connect()
        
        # Запуск веб-сервера для keepalive
        web_runner = await start_web_server()
        
        # Инициализация планировщика
        scheduler = init_scheduler(bot)
        await scheduler.start()
        
        # Регистрация обработчиков
        dp.include_router(admin.router)
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
        # Перезапуск при критических ошибках
        await asyncio.sleep(5)
        raise
    finally:
        # Остановка планировщика
        if scheduler:
            await scheduler.stop()
        
        # Остановка веб-сервера
        if web_runner:
            await web_runner.cleanup()
        
        # Закрытие соединений
        await db.disconnect()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        # Автоматический перезапуск через несколько секунд
        exit(1)
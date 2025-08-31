import logging
from aiogram import Router, Bot, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_USER_ID
from database.connection import db
from services.backup_service import backup_service

logger = logging.getLogger(__name__)
router = Router()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return user_id == ADMIN_USER_ID

@router.message(Command("backup"))
async def backup_command(message: Message):
    """Команда для создания бэкапа базы данных"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        # Уведомляем о начале создания бэкапа
        status_message = await message.answer("🔄 Создаю бэкап базы данных...")
        
        # Создаем сжатый бэкап
        compressed_backup, filename, metadata = await backup_service.create_compressed_backup()
        
        # Форматируем сводку
        summary = backup_service.format_backup_summary(metadata)
        
        # Создаем файл для отправки
        input_file = BufferedInputFile(
            file=compressed_backup.getvalue(),
            filename=filename
        )
        
        # Отправляем файл с описанием
        await message.answer_document(
            document=input_file,
            caption=summary
        )
        
        # Удаляем статусное сообщение
        await status_message.delete()
        
        logger.info(f"Manual backup sent to admin {message.from_user.id}")
        
    except Exception as e:
        logger.error(f"Error creating manual backup: {e}")
        await message.answer(f"❌ Ошибка создания бэкапа: {e}")

async def send_daily_backup(bot: Bot):
    """Отправка ежедневного бэкапа админу"""
    try:
        logger.info("Starting daily backup creation")
        
        # Создаем сжатый бэкап
        compressed_backup, filename, metadata = await backup_service.create_compressed_backup()
        
        # Форматируем сводку
        summary = f"📅 Ежедневный автоматический бэкап\n\n{backup_service.format_backup_summary(metadata)}"
        
        # Создаем файл для отправки
        input_file = BufferedInputFile(
            file=compressed_backup.getvalue(),
            filename=filename
        )
        
        # Отправляем бэкап админу
        await bot.send_document(
            chat_id=ADMIN_USER_ID,
            document=input_file,
            caption=summary
        )
        
        logger.info("Daily backup sent successfully")
        
    except Exception as e:
        logger.error(f"Error sending daily backup: {e}")
        
        # Пытаемся отправить уведомление об ошибке
        try:
            await bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"❌ Ошибка создания ежедневного бэкапа:\n\n{e}"
            )
        except Exception as notify_error:
            logger.error(f"Failed to send error notification: {notify_error}")

@router.message(Command("broadcast"))
async def broadcast_command(message: Message, state: FSMContext):
    """Команда для рассылки сообщений всем пользователям"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    # Проверяем, есть ли текст после команды
    text = message.text.replace("/broadcast", "").strip()
    
    if text:
        # Если текст есть, сразу отправляем рассылку
        await send_broadcast(message.bot, text, message)
    else:
        # Если текста нет, запрашиваем его
        await state.set_state(BroadcastStates.waiting_for_message)
        await message.answer(
            "📢 Введите текст для рассылки всем пользователям:\n\n"
            "Отправьте /cancel для отмены."
        )

@router.message(BroadcastStates.waiting_for_message, F.text)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка введенного текста для рассылки"""
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Рассылка отменена.")
        return
    
    await send_broadcast(message.bot, message.text, message)
    await state.clear()

async def send_broadcast(bot: Bot, text: str, admin_message: Message):
    """Отправка рассылки всем пользователям"""
    try:
        # Получаем всех пользователей из базы данных
        async with db.pool.acquire() as conn:
            users = await conn.fetch("SELECT DISTINCT user_id FROM users WHERE user_id IS NOT NULL")
        
        if not users:
            await admin_message.answer("📭 Нет пользователей для рассылки.")
            return
        
        sent_count = 0
        failed_count = 0
        
        await admin_message.answer(f"📤 Начинаю рассылку для {len(users)} пользователей...")
        
        for user_row in users:
            user_id = user_row['user_id']  # asyncpg возвращает Record объекты
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"📢 Сообщение от администратора:\n\n{text}"
                )
                sent_count += 1
                logger.info(f"Broadcast sent to user {user_id}")
                
            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to send broadcast to user {user_id}: {e}")
        
        # Отчет о рассылке
        report = (
            f"✅ Рассылка завершена!\n\n"
            f"📊 Статистика:\n"
            f"• Отправлено: {sent_count}\n"
            f"• Не доставлено: {failed_count}\n"
            f"• Всего пользователей: {len(users)}"
        )
        
        await admin_message.answer(report)
        
    except Exception as e:
        logger.error(f"Error in broadcast: {e}")
        await admin_message.answer(f"❌ Ошибка при рассылке: {e}")

@router.message(Command("admin"))
async def admin_commands(message: Message):
    """Показать доступные админ команды"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    admin_help = (
        "🔧 <b>Команды администратора:</b>\n\n"
        "📢 <code>/broadcast [текст]</code> - Рассылка сообщения всем пользователям\n"
        "📊 <code>/stats</code> - Статистика бота\n"
        "💾 <code>/backup</code> - Создать бэкап базы данных\n"
        "🔄 <code>/migrate</code> - Миграция данных к шифрованию\n"
        "🔧 <code>/admin</code> - Показать эту справку\n\n"
        "ℹ️ Ежедневные бэкапы отправляются автоматически в 12:00 UTC"
    )
    
    await message.answer(admin_help, parse_mode="HTML")

@router.message(Command("stats"))
async def bot_stats(message: Message):
    """Статистика бота"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        async with db.pool.acquire() as conn:
            # Общее количество пользователей
            users_count = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM users")
            
            # Количество задач
            tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")
            
            # Количество напоминаний
            reminders_count = await conn.fetchval("SELECT COUNT(*) FROM reminders")
            active_reminders = await conn.fetchval("SELECT COUNT(*) FROM reminders WHERE is_active = TRUE")
            
            # Количество записей дневника
            diary_entries_count = await conn.fetchval("SELECT COUNT(*) FROM diary_entries")
            
        
        stats_text = (
            f"📊 <b>Статистика бота:</b>\n\n"
            f"👥 Пользователей: {users_count or 0}\n\n"
            f"📝 <b>Задачи:</b>\n"
            f"• Всего: {tasks_count or 0}\n\n"
            f"⏰ <b>Напоминания:</b>\n"
            f"• Всего: {reminders_count or 0}\n"
            f"• Активных: {active_reminders or 0}\n\n"
            f"📖 <b>Дневник:</b>\n"
            f"• Всего записей: {diary_entries_count or 0}\n\n"
        )
        
        await message.answer(stats_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await message.answer(f"❌ Ошибка получения статистики: {e}")

@router.message(Command("migrate"))
async def migrate_data_command(message: Message):
    """Команда для миграции существующих данных к шифрованию"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        await message.answer("🔄 Начинаю миграцию данных к шифрованию...")
        
        # Запускаем миграцию
        await db.migrate_existing_data_to_encrypted()
        
        await message.answer(
            "✅ Миграция данных завершена!\n\n"
            "Все существующие данные (задачи, напоминания, записи дневника) "
            "теперь зашифрованы и защищены."
        )
        
        logger.info(f"Data migration completed by admin {message.from_user.id}")
        
    except Exception as e:
        logger.error(f"Error during data migration: {e}")
        await message.answer(f"❌ Ошибка при миграции: {e}")
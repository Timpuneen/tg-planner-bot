import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, time, timedelta
import calendar
import pytz
import logging

from services.timezone_service import convert_user_time_to_scheduler_timezone, get_scheduler_timezone, get_user_time
from database.connection import db
from services.openai_service import generate_daily_motivation
from services.encryption_service import decrypt_text
from aiogram import types
from handlers.admin import send_daily_backup

logger = logging.getLogger(__name__)


def is_last_day_of_month(date: datetime) -> bool:
    """Проверяет, является ли дата последним днём месяца"""
    last_day = calendar.monthrange(date.year, date.month)[1]
    return date.day == last_day


def parse_cron_expression(cron_expr: str, timezone) -> CronTrigger | None:
    """
    Парсит cron-выражение и возвращает Trigger.
    Поддержка L (последний день месяца).
    """
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")

    minute, hour, day, month, day_of_week = parts

    # Если есть L в поле day → делаем CronTrigger на каждый день
    if day == "L":
        return CronTrigger(
            minute=minute,
            hour=hour,
            day="*",
            month=month,
            day_of_week=day_of_week,
            timezone=timezone
        )
    else:
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=timezone
        )


class SchedulerService:
    def __init__(self, bot):
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.bot = bot

    async def start(self):
        """Запуск планировщика"""
        self.scheduler.start()
        logger.info("Scheduler started")

        await self.load_active_reminders()
        await self.setup_system_tasks()

    async def stop(self):
        """Остановка планировщика"""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def _decrypt_reminder_text(self, encrypted_text: str, reminder_id: int) -> str:
        """Вспомогательный метод для расшифровки текста напоминаний"""
        try:
            return decrypt_text(encrypted_text)
        except Exception as e:
            logger.error(f"Failed to decrypt reminder {reminder_id}: {e}")
            return "[Ошибка расшифровки]"

    async def _load_reminders_by_type(self, reminder_type: str) -> list:
        """Загрузка напоминаний определенного типа"""
        conditions = {
            'once': "r.reminder_type = 'once' AND r.is_active = TRUE AND r.trigger_time > NOW()",
            'recurring': "r.reminder_type = 'recurring' AND r.is_active = TRUE"
        }
        
        fields = {
            'once': "r.reminder_id, r.user_id, r.text, r.trigger_time, u.timezone",
            'recurring': "r.reminder_id, r.user_id, r.text, r.cron_expression, u.timezone"
        }

        async with db.pool.acquire() as conn:
            return await conn.fetch(f"""
                SELECT {fields[reminder_type]}
                FROM reminders r
                JOIN users u ON r.user_id = u.user_id
                WHERE {conditions[reminder_type]}
            """)

    async def load_active_reminders(self):
        """Загрузка всех активных напоминаний из базы данных с расшифровкой"""
        try:
            # Загружаем разовые напоминания
            once_reminders = await self._load_reminders_by_type('once')
            for reminder in once_reminders:
                decrypted_text = self._decrypt_reminder_text(reminder['text'], reminder['reminder_id'])
                scheduler_time = convert_user_time_to_scheduler_timezone(
                    reminder['trigger_time'],
                    reminder['timezone'],
                    get_scheduler_timezone()
                )
                await self.add_once_reminder(
                    reminder['reminder_id'],
                    reminder['user_id'],
                    decrypted_text,
                    scheduler_time
                )

            # Загружаем повторяющиеся напоминания
            recurring_reminders = await self._load_reminders_by_type('recurring')
            for reminder in recurring_reminders:
                decrypted_text = self._decrypt_reminder_text(reminder['text'], reminder['reminder_id'])
                await self.add_recurring_reminder_with_timezone(
                    reminder['reminder_id'],
                    reminder['user_id'],
                    decrypted_text,
                    reminder['cron_expression'],
                    reminder['timezone']
                )

            logger.info(f"Loaded {len(once_reminders)} once and {len(recurring_reminders)} recurring reminders")

        except Exception as e:
            logger.error(f"Error loading active reminders: {e}")

    async def add_recurring_reminder_with_timezone(self, reminder_id: int, user_id: int,
                                                   text: str, cron_expression: str, user_timezone: str):
        """Добавление повторяющегося напоминания с учетом часового пояса"""
        try:
            job_id = f"reminder_recurring_{reminder_id}"
            user_tz = pytz.timezone(user_timezone)
            trigger = parse_cron_expression(cron_expression, user_tz)

            self.scheduler.add_job(
                self._wrapped_send_recurring_reminder,
                trigger=trigger,
                args=[user_id, text, reminder_id, cron_expression],
                id=job_id,
                replace_existing=True
            )

            logger.info(f"Added recurring reminder {reminder_id} for user {user_id} in timezone {user_timezone}")

        except Exception as e:
            logger.error(f"Error adding recurring reminder with timezone: {e}")

    def _get_reminder_message_by_priority(self, user_timezone: str) -> tuple[str, str]:
        """
        Определяет тип напоминания по приоритету: год > месяц > неделя > день
        Возвращает (тип_периода, текст_сообщения)
        """
        current_time = get_user_time(user_timezone)
        today = current_time.date()
        
        messages = {
            'year': "🎊 Год подходит к концу! Время подвести итоги года и поставить цели на следующий год.\n\nОтметьте выполненные задачи года и составьте новые!",
            'month': "📅 Месяц подходит к концу! Время проанализировать достижения месяца.\n\nОтметьте выполненные задачи месяца и составьте новые!",
            'week': "📊 Неделя завершается! Отличное время подвести итоги недели.\n\nОтметьте выполненные задачи недели и составьте новые!",
            'day': "🌅 День подходит к концу! Время подвести итоги дня.\n\nОтметьте выполненные задачи дня и составьте новые!"
        }
        
        # Проверяем приоритеты
        if today.month == 12 and today.day == 31:
            return "год", messages['year']
        elif is_last_day_of_month(current_time):
            return "месяц", messages['month']
        elif today.weekday() == 6:  # воскресенье
            return "неделя", messages['week']
        else:
            return "день", messages['day']

    async def _wrapped_send_recurring_reminder(self, user_id: int, text: str, reminder_id: int, cron_expression: str):
        """
        Обертка для отправки повторяющихся напоминаний.
        Теперь определяет тип напоминания по приоритету.
        """
        try:
            # Получаем данные пользователя и напоминания одним запросом
            async with db.pool.acquire() as conn:
                user_reminder_data = await conn.fetchrow("""
                    SELECT u.timezone, r.is_built_in
                    FROM users u
                    JOIN reminders r ON r.reminder_id = $1
                    WHERE u.user_id = $2
                """, reminder_id, user_id)
                
            if not user_reminder_data:
                logger.error(f"User {user_id} or reminder {reminder_id} not found")
                return
                
            if user_reminder_data['is_built_in']:
                # Для встроенного напоминания определяем приоритет
                period_type, smart_message = self._get_reminder_message_by_priority(user_reminder_data['timezone'])
                await self.send_reminder(user_id, smart_message, reminder_id, 'recurring')
                logger.info(f"Sent smart {period_type} reminder to user {user_id}")
            else:
                # Для обычных напоминаний с L проверяем последний день месяца
                if "L" in cron_expression:
                    current_time = get_user_time(user_reminder_data['timezone'])
                    if not is_last_day_of_month(current_time):
                        return  # пропускаем не последний день месяца
                
                await self.send_reminder(user_id, text, reminder_id, 'recurring')
                
        except Exception as e:
            logger.error(f"Error in wrapped recurring reminder {reminder_id}: {e}")

    async def add_once_reminder(self, reminder_id: int, user_id: int, text: str, trigger_time: datetime):
        """Добавление разового напоминания"""
        try:
            job_id = f"reminder_once_{reminder_id}"
            scheduler_tz = pytz.timezone(get_scheduler_timezone())
            aware_trigger_time = scheduler_tz.localize(trigger_time)

            self.scheduler.add_job(
                self.send_reminder,
                trigger=DateTrigger(run_date=aware_trigger_time),
                args=[user_id, text, reminder_id, 'once'],
                id=job_id,
                replace_existing=True
            )

            logger.info(f"Added once reminder {reminder_id} for user {user_id} at {aware_trigger_time}")

        except Exception as e:
            logger.error(f"Error adding once reminder: {e}")

    async def remove_reminder(self, reminder_id: int, reminder_type: str):
        """Удаление напоминания"""
        try:
            job_id = f"reminder_{reminder_type}_{reminder_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"Removed {reminder_type} reminder {reminder_id}")

        except Exception as e:
            logger.error(f"Error removing reminder: {e}")

    async def send_reminder(self, user_id: int, text: str, reminder_id: int, reminder_type: str):
        """Отправка напоминания пользователю"""
        try:
            message = f"⏰ Напоминание:\n\n{text}"
            await self.bot.send_message(user_id, message)

            if reminder_type == 'once':
                async with db.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM reminders WHERE reminder_id = $1",
                        reminder_id
                    )
                logger.info(f"Deleted once reminder {reminder_id}")

        except Exception as e:
            logger.error(f"Error sending reminder: {e}")

    async def setup_system_tasks(self):
        """Системные задачи"""
        system_jobs = [
            ("daily_motivation", self.send_daily_motivation, CronTrigger(hour=8, minute=0)),
            ("evening_review", self.send_evening_review, CronTrigger(hour=20, minute=0)),
            ("overdue_check", self.check_overdue_tasks, CronTrigger(hour=0, minute=30)),
            ("daily_backup", self.send_daily_backup, CronTrigger(hour=12, minute=0)) 
        ]

        try:
            for job_id, func, trigger in system_jobs:
                self.scheduler.add_job(
                    func,
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True
                )

            logger.info("System tasks setup completed")

        except Exception as e:
            logger.error(f"Error setting up system tasks: {e}")

    async def send_daily_motivation(self):
        """Утренние мотивации (упрощенная версия без кнопок)"""
        try:
            async with db.pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id FROM users")

            motivation = await generate_daily_motivation()
            message = f"🌅 Доброе утро!\n\n{motivation}"

            for user in users:
                try:
                    await self.bot.send_message(user['user_id'], message)
                except Exception as e:
                    logger.error(f"Error sending morning message to user {user['user_id']}: {e}")

        except Exception as e:
            logger.error(f"Error sending daily motivation: {e}")

    async def send_evening_review(self):
        """Вечерние ревью с записями дневника и задачами на сегодня"""
        try:
            async with db.pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id, timezone FROM users")

            for user in users:
                try:
                    await self.send_user_evening_review(user['user_id'])
                except Exception as e:
                    logger.error(f"Error sending evening review to user {user['user_id']}: {e}")

        except Exception as e:
            logger.error(f"Error in send_evening_review: {e}")

    def _decrypt_content_safely(self, content: str, content_type: str, user_id: int) -> str:
        """Безопасная расшифровка контента с обработкой ошибок"""
        try:
            return decrypt_text(content)
        except Exception as e:
            logger.error(f"Failed to decrypt {content_type} for user {user_id}: {e}")
            return "[Ошибка расшифровки]"

    def _group_tasks_by_status(self, tasks: list, user_id: int) -> dict:
        """Группировка задач по статусам с расшифровкой"""
        groups = {
            'completed': [],
            'failed': [],
            'active': [],
            'overdue': []
        }
        
        for task in tasks:
            decrypted_text = self._decrypt_content_safely(task['text'], 'task', user_id)
            task_dict = dict(task)
            task_dict['text'] = decrypted_text
            
            if task['status'] in groups:
                groups[task['status']].append(task_dict)
        
        return groups

    def _format_task_group(self, tasks: list, title: str, icon: str) -> str:
        """Форматирование группы задач для отображения"""
        if not tasks:
            return ""
        
        result = f"{icon} {title}:\n"
        for task in tasks:
            category_text = f" ({task['category']})" if task['category'] else ""
            result += f"  • {task['text']}{category_text}\n"
        result += "\n"
        return result

    async def send_user_evening_review(self, user_id: int):
        """Формирует и отправляет ревью дня конкретному пользователю с расшифровкой данных"""
        try:
            async with db.pool.acquire() as conn:
                user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
                if not user:
                    logger.error(f"User {user_id} not found")
                    return

            current_time = get_user_time(user['timezone'])
            today = current_time.date()
            yesterday = today - timedelta(days=1)
            
            today_utc_start, today_utc_end = self._get_day_utc_bounds(today, user['timezone'])
            yesterday_utc_start, yesterday_utc_end = self._get_day_utc_bounds(yesterday, user['timezone'])

            # Получаем данные одним блоком запросов
            async with db.pool.acquire() as conn:
                # Записи дневника
                diary_entries = await conn.fetch(
                    """SELECT content, created_at FROM diary_entries 
                       WHERE user_id = $1 AND entry_date = $2
                       ORDER BY created_at ASC""",
                    user_id, today
                )

                # Релевантные задачи для ревью
                review_tasks = await conn.fetch(
                    """SELECT text, category, status, deadline, completed_at FROM tasks 
                       WHERE user_id = $1 AND (
                           (status = 'completed' AND completed_at >= $2 AND completed_at <= $3)
                           OR (status = 'failed' AND completed_at >= $2 AND completed_at <= $3)
                           OR (status = 'active' AND deadline IS NOT NULL AND deadline >= $2 AND deadline <= $3)
                           OR (status = 'overdue' AND deadline IS NOT NULL AND deadline >= $4 AND deadline <= $5)
                       )
                       ORDER BY status, category NULLS LAST""",
                    user_id, today_utc_start, today_utc_end, yesterday_utc_start, yesterday_utc_end
                )

            # Формируем сообщение
            review_text = f"🌙 Ревью дня {today.strftime('%d.%m.%Y')}\n\n"

            # Записи дневника с расшифровкой
            if diary_entries:
                review_text += "📝 Записи дневника:\n"
                for entry in diary_entries:
                    time_str = entry['created_at'].strftime('%H:%M')
                    decrypted_content = self._decrypt_content_safely(entry['content'], 'diary entry', user_id)
                    # Обрезаем длинные записи
                    content = decrypted_content[:150] + ('...' if len(decrypted_content) > 150 else '')
                    review_text += f"• {time_str} - {content}\n"
                review_text += "\n"
            else:
                review_text += "📝 Записей дневника за день нет\n\n"

            # Задачи для ревью с расшифровкой и группировкой
            if review_tasks:
                review_text += "📋 Задачи:\n"
                task_groups = self._group_tasks_by_status(review_tasks, user_id)
                
                # Форматируем каждую группу задач
                review_text += self._format_task_group(task_groups['completed'], "Выполнено сегодня", "✅")
                review_text += self._format_task_group(task_groups['failed'], "Отмечено невыполненными сегодня", "❌")
                review_text += self._format_task_group(task_groups['active'], "Активные с дедлайном сегодня", "🔥")
                review_text += self._format_task_group(task_groups['overdue'], "Стали просроченными сегодня", "⚠️")
            else:
                review_text += "📋 Релевантных задач за день нет\n\n"

            review_text += "Хорошего отдыха! 😴"

            # Отправляем сообщение
            await self.bot.send_message(user_id, review_text)

        except Exception as e:
            logger.error(f"Error sending evening review to user {user_id}: {e}")

    def _get_day_utc_bounds(self, date, timezone_str):
        """Получает UTC границы дня для заданной даты в часовом поясе пользователя"""
        user_tz = pytz.timezone(timezone_str)
        
        # Начало и конец дня в пользовательском часовом поясе
        day_start_local = user_tz.localize(datetime.combine(date, time.min))
        day_end_local = user_tz.localize(datetime.combine(date, time.max))
        
        # Конвертируем в UTC и убираем timezone info для БД
        day_start_utc = day_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        day_end_utc = day_end_local.astimezone(pytz.UTC).replace(tzinfo=None)
        
        return day_start_utc, day_end_utc

    async def check_overdue_tasks(self):
        """Проверка и пометка просроченных задач"""
        TASK_LIMITS = {
            'active': 50,
            'completed': 50,
            'failed': 25,
            'overdue': 25
        }
        
        try:
            async with db.pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id, timezone FROM users")
                
                for user in users:
                    try:
                        user_tz = pytz.timezone(user['timezone'])
                        current_time = get_user_time(user['timezone'])
                        current_utc = self._normalize_datetime_for_db(current_time)
                        
                        # Получаем просроченные задачи и текущее количество
                        overdue_tasks, overdue_count = await asyncio.gather(
                            conn.fetch(
                                """SELECT task_id FROM tasks 
                                   WHERE user_id = $1 AND status = 'active' 
                                   AND deadline IS NOT NULL AND deadline < $2""",
                                user['user_id'], current_utc
                            ),
                            conn.fetchval(
                                "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND status = 'overdue'",
                                user['user_id']
                            )
                        )
                        
                        if overdue_tasks:
                            tasks_to_mark = len(overdue_tasks)
                            
                            # Если превышаем лимит, удаляем самые старые просроченные
                            if overdue_count + tasks_to_mark > TASK_LIMITS['overdue']:
                                delete_count = (overdue_count + tasks_to_mark) - TASK_LIMITS['overdue']
                                await conn.execute(
                                    """DELETE FROM tasks WHERE task_id IN (
                                        SELECT task_id FROM tasks 
                                        WHERE user_id = $1 AND status = 'overdue'
                                        ORDER BY marked_overdue_at ASC LIMIT $2
                                    )""",
                                    user['user_id'], delete_count
                                )
                            
                            # Помечаем задачи как просроченные
                            task_ids = [task['task_id'] for task in overdue_tasks]
                            await conn.execute(
                                """UPDATE tasks SET status = 'overdue', marked_overdue_at = NOW() 
                                   WHERE task_id = ANY($1::int[])""",
                                task_ids
                            )
                            
                            logger.info(f"Marked {len(task_ids)} tasks as overdue for user {user['user_id']}")
                    
                    except Exception as e:
                        logger.error(f"Error checking overdue tasks for user {user['user_id']}: {e}")
        
        except Exception as e:
            logger.error(f"Error in check_overdue_tasks: {e}")

    def _normalize_datetime_for_db(self, dt):
        """Нормализует datetime для сохранения в базу данных"""
        if dt is None:
            return None
        
        # Если datetime содержит timezone info, конвертируем в UTC и убираем timezone info
        if dt.tzinfo is not None:
            utc_dt = dt.astimezone(pytz.UTC)
            return utc_dt.replace(tzinfo=None)
        
        # Если уже naive, возвращаем как есть
        return dt

    async def send_daily_backup(self):
        """Ежедневная отправка бэкапа админу в 12:00 UTC"""
        try:
            await send_daily_backup(self.bot)
            logger.info("Daily backup task completed")
        except Exception as e:
            logger.error(f"Error in daily backup task: {e}")

# Глобальный экземпляр
scheduler_service = None


def get_scheduler():
    return scheduler_service


def init_scheduler(bot):
    global scheduler_service
    scheduler_service = SchedulerService(bot)
    return scheduler_service
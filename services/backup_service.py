import json
import gzip
import logging
from datetime import datetime, timezone, date, time
from typing import Dict, List, Any, Optional
from io import BytesIO
import asyncio

from database.connection import db

logger = logging.getLogger(__name__)

class BackupService:
    """Сервис для создания зашифрованных бэкапов базы данных"""
    
    def __init__(self):
        self.backup_version = "1.0"
    
    async def _export_users(self) -> List[Dict[str, Any]]:
        """Экспорт данных пользователей"""
        async with db.pool.acquire() as conn:
            users = await conn.fetch("""
                SELECT user_id, timezone, created_at 
                FROM users 
                ORDER BY user_id
            """)
        
        return [dict(user) for user in users]
    
    async def _export_tasks(self) -> List[Dict[str, Any]]:
        """Экспорт задач (зашифрованные данные)"""
        async with db.pool.acquire() as conn:
            tasks = await conn.fetch("""
                SELECT task_id, user_id, text, category, deadline, status, 
                       created_at, completed_at, marked_overdue_at
                FROM tasks 
                ORDER BY user_id, task_id
            """)
        
        return [dict(task) for task in tasks]
    
    async def _export_reminders(self) -> List[Dict[str, Any]]:
        """Экспорт напоминаний (зашифрованные данные)"""
        async with db.pool.acquire() as conn:
            reminders = await conn.fetch("""
                SELECT reminder_id, user_id, text, reminder_type, trigger_time, 
                       cron_expression, is_active, is_built_in, created_at
                FROM reminders 
                ORDER BY user_id, reminder_id
            """)
        
        return [dict(reminder) for reminder in reminders]
    
    async def _export_diary_entries(self) -> List[Dict[str, Any]]:
        """Экспорт записей дневника (зашифрованные данные)"""
        async with db.pool.acquire() as conn:
            entries = await conn.fetch("""
                SELECT entry_id, user_id, entry_date, content, created_at, updated_at, is_edited
                FROM diary_entries 
                ORDER BY user_id, entry_date, entry_id
            """)
        
        return [dict(entry) for entry in entries]
    
    async def _export_task_categories(self) -> List[Dict[str, Any]]:
        """Экспорт категорий задач"""
        async with db.pool.acquire() as conn:
            categories = await conn.fetch("""
                SELECT category_id, user_id, name, created_at
                FROM task_categories 
                ORDER BY user_id, category_id
            """)
        
        return [dict(category) for category in categories]
    
    async def _get_database_statistics(self) -> Dict[str, Any]:
        """Получение статистики базы данных"""
        async with db.pool.acquire() as conn:
            stats = {}
            
            # Количество записей в каждой таблице
            tables = ['users', 'tasks', 'reminders', 'diary_entries', 'task_categories']
            for table in tables:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                stats[f"{table}_count"] = count
            
            # Дополнительная статистика
            stats.update({
                'active_reminders': await conn.fetchval("SELECT COUNT(*) FROM reminders WHERE is_active = TRUE"),
                'completed_tasks': await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status = 'completed'"),
                'active_tasks': await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status = 'active'"),
                'overdue_tasks': await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE status = 'overdue'"),
            })
        
        return stats
    
    async def create_backup(self) -> Dict[str, Any]:
        """Создание полного бэкапа базы данных"""
        logger.info("Starting database backup creation")
        
        try:
            # Экспортируем все данные параллельно для ускорения
            users_task = asyncio.create_task(self._export_users())
            tasks_task = asyncio.create_task(self._export_tasks())
            reminders_task = asyncio.create_task(self._export_reminders())
            diary_task = asyncio.create_task(self._export_diary_entries())
            categories_task = asyncio.create_task(self._export_task_categories())
            stats_task = asyncio.create_task(self._get_database_statistics())
            
            # Ждем завершения всех задач
            users, tasks, reminders, diary_entries, categories, stats = await asyncio.gather(
                users_task, tasks_task, reminders_task, diary_task, categories_task, stats_task
            )
            
            # Формируем структуру бэкапа
            backup_data = {
                'metadata': {
                    'version': self.backup_version,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'database_statistics': stats
                },
                'data': {
                    'users': users,
                    'tasks': tasks,
                    'reminders': reminders,
                    'diary_entries': diary_entries,
                    'task_categories': categories
                }
            }
            
            logger.info(f"Backup created successfully. Stats: {stats}")
            return backup_data
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            raise
    
    def _serialize_backup_data(self, backup_data: Dict[str, Any]) -> str:
        """Сериализация данных бэкапа в JSON с обработкой datetime"""
        def json_serializer(obj):
            """Кастомный сериализатор для JSON"""
            # Обработка различных типов datetime объектов
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, date):  # Исправлено: добавлен импорт date и правильная проверка
                return obj.isoformat()
            elif isinstance(obj, time):  # Добавлена обработка времени
                return obj.isoformat()
            # Обработка других возможных типов
            elif hasattr(obj, 'isoformat') and callable(getattr(obj, 'isoformat')):
                return obj.isoformat()
            # Обработка Decimal если используется
            elif hasattr(obj, '__float__'):
                return float(obj)
            
            # Для отладки - логируем неизвестные типы
            logger.warning(f"Unknown object type for JSON serialization: {type(obj)} - {obj}")
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        try:
            return json.dumps(backup_data, ensure_ascii=False, indent=2, default=json_serializer)
        except Exception as e:
            logger.error(f"JSON serialization failed: {e}")
            # Дополнительная диагностика
            logger.error(f"Backup data keys: {list(backup_data.keys())}")
            raise
    
    def compress_backup(self, backup_json: str) -> BytesIO:
        """Сжатие бэкапа с помощью gzip"""
        # Создаем BytesIO объект для хранения сжатых данных
        compressed_buffer = BytesIO()
        
        # Сжимаем JSON данные
        with gzip.GzipFile(fileobj=compressed_buffer, mode='wb') as gz_file:
            gz_file.write(backup_json.encode('utf-8'))
        
        # Возвращаем указатель в начало буфера
        compressed_buffer.seek(0)
        return compressed_buffer
    
    def generate_backup_filename(self) -> str:
        """Генерация имени файла бэкапа"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"tg_planner_backup_{timestamp}.json.gz"
    
    async def create_compressed_backup(self) -> tuple[BytesIO, str, Dict[str, Any]]:
        """Создание сжатого бэкапа для отправки"""
        try:
            # Создаем бэкап
            backup_data = await self.create_backup()
            
            # Сериализуем в JSON
            backup_json = self._serialize_backup_data(backup_data)
            
            # Сжимаем
            compressed_backup = self.compress_backup(backup_json)
            
            # Генерируем имя файла
            filename = self.generate_backup_filename()
            
            logger.info(f"Compressed backup created: {filename}")
            return compressed_backup, filename, backup_data['metadata']
            
        except Exception as e:
            logger.error(f"Failed to create compressed backup: {e}")
            raise
    
    def format_backup_summary(self, metadata: Dict[str, Any]) -> str:
        """Форматирование сводки бэкапа для отправки админу"""
        stats = metadata.get('database_statistics', {})
        created_at = datetime.fromisoformat(metadata['created_at'].replace('Z', '+00:00'))
        
        summary = f"""📋 Бэкап базы данных TG-Planner (зашифрованный)
🕐 Создан: {created_at.strftime('%d.%m.%Y %H:%M:%S')} UTC

📊 Статистика:
👥 Пользователей: {stats.get('users_count', 0)}

📝 Задачи:
• Всего: {stats.get('tasks_count', 0)}
• Активных: {stats.get('active_tasks', 0)}
• Выполненных: {stats.get('completed_tasks', 0)}
• Просроченных: {stats.get('overdue_tasks', 0)}

⏰ Напоминания:
• Всего: {stats.get('reminders_count', 0)}
• Активных: {stats.get('active_reminders', 0)}

📖 Дневник: {stats.get('diary_entries_count', 0)} записей
📂 Категории: {stats.get('task_categories_count', 0)}

🔐 Личные данные в бэкапе зашифрованы
Версия бэкапа: {metadata.get('version', 'unknown')}"""

        return summary

# Глобальный экземпляр сервиса
backup_service = BackupService()
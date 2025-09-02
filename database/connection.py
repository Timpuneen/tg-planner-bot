import asyncpg
from typing import Optional, List, Dict, Any
from config import DATABASE_URL
import logging
import asyncio

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
        # Семафор для ограничения одновременных операций
        self._operation_semaphore = asyncio.Semaphore(15)
    
    async def connect(self):
        """Создание пула соединений с базой данных"""
        try:
            # ИСПРАВЛЕНИЕ: Настройка параметров пула соединений
            self.pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=5,           # Минимальное количество соединений
                max_size=20,          # Максимальное количество соединений
                max_queries=50000,    # Максимальное количество запросов на соединение
                max_inactive_connection_lifetime=300,  # 5 минут
                command_timeout=60,   # Таймаут команд в секундах
                server_settings={
                    'jit': 'off'      # Отключение JIT для стабильности
                }
            )
            await self.create_tables()
            logger.info("Database connection established with pool configuration")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    async def disconnect(self):
        """Закрытие пула соединений"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection closed")
    
    def _encrypt_text(self, text: str) -> str:
        """Вспомогательный метод для шифрования"""
        from services.encryption_service import encrypt_text
        return encrypt_text(text)
    
    def _decrypt_text(self, encrypted_text: str, item_id: int = None, item_type: str = "item") -> str:
        """Вспомогательный метод для расшифровки"""
        from services.encryption_service import decrypt_text
        try:
            return decrypt_text(encrypted_text)
        except Exception as e:
            logger.error(f"Failed to decrypt {item_type} {item_id}: {e}")
            return "[Ошибка расшифровки]"
    
    def _decrypt_items(self, items: List[Any], text_field: str, id_field: str, item_type: str) -> List[Dict[str, Any]]:
        """Универсальный метод для расшифровки списка элементов"""
        decrypted_items = []
        for item in items:
            item_dict = dict(item)
            item_dict[text_field] = self._decrypt_text(
                item[text_field], 
                item[id_field], 
                item_type
            )
            decrypted_items.append(item_dict)
        return decrypted_items

    # НОВЫЙ МЕТОД: Безопасное выполнение операций с БД
    async def safe_execute(self, operation_func, max_retries=3, *args, **kwargs):
        """
        Безопасное выполнение операций с базой данных
        
        Args:
            operation_func: Функция для выполнения
            max_retries: Максимальное количество повторных попыток
        """
        async with self._operation_semaphore:
            for attempt in range(max_retries + 1):
                try:
                    return await operation_func(*args, **kwargs)
                    
                except asyncpg.InterfaceError as e:
                    error_msg = str(e).lower()
                    if "another operation is in progress" in error_msg:
                        if attempt < max_retries:
                            # Экспоненциальная задержка: 0.1, 0.2, 0.4 секунды
                            delay = 0.1 * (2 ** attempt)
                            logger.warning(f"Database busy, retrying in {delay}s (attempt {attempt + 1}/{max_retries + 1})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(f"Database operation failed after {max_retries} retries: {e}")
                            raise
                    else:
                        # Другие InterfaceError не ретраим
                        raise
                        
                except asyncpg.PostgresError as e:
                    # PostgreSQL ошибки (дедлоки, конфликты и т.д.)
                    if attempt < max_retries and e.sqlstate in ('40001', '40P01', '55P03'):
                        # Коды ошибок: serialization_failure, deadlock_detected, lock_not_available
                        delay = 0.05 * (2 ** attempt)
                        logger.warning(f"Database conflict, retrying in {delay}s: {e.sqlstate}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise
                        
                except Exception as e:
                    # Все остальные ошибки не ретраим
                    logger.error(f"Unexpected database error: {e}")
                    raise
    
    async def create_tables(self):
        """Создание таблиц в базе данных"""
        tables_sql = [
            # Таблица пользователей
            """CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                timezone VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            
            # Таблица напоминаний
            """CREATE TABLE IF NOT EXISTS reminders (
                reminder_id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                text TEXT NOT NULL,
                reminder_type VARCHAR(20) NOT NULL,
                trigger_time TIMESTAMP,
                cron_expression VARCHAR(100),
                is_active BOOLEAN DEFAULT TRUE,
                is_built_in BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            
            # Таблица задач
            """CREATE TABLE IF NOT EXISTS tasks (
                task_id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                text TEXT NOT NULL,
                category VARCHAR(100),
                deadline TIMESTAMP,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                marked_overdue_at TIMESTAMP
            )""",

            # Таблица для категорий задач
            """CREATE TABLE IF NOT EXISTS task_categories (
                category_id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                name VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, name)
            )""",

            # Таблица записей дневника
            """CREATE TABLE IF NOT EXISTS diary_entries (
                entry_id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                entry_date DATE NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                is_edited BOOLEAN DEFAULT FALSE
            )"""
        ]
        
        indexes_sql = [
            "CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline) WHERE deadline IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_task_categories_user ON task_categories(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_diary_entries_user_date ON diary_entries(user_id, entry_date DESC)"
        ]
        
        # ИСПРАВЛЕНИЕ: Используем отдельные соединения для создания таблиц
        for sql in tables_sql + indexes_sql:
            async with self.pool.acquire() as conn:
                await conn.execute(sql)
        
        logger.info("Database tables created/verified")
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С ЗАДАЧАМИ ===
    
    async def create_task(self, user_id: int, text: str, category: str = None, deadline=None) -> int:
        """Создание новой задачи с шифрованием"""
        async def _create_task():
            encrypted_text = self._encrypt_text(text)
            async with self.pool.acquire() as conn:
                task_id = await conn.fetchval(
                    """INSERT INTO tasks (user_id, text, category, deadline) 
                       VALUES ($1, $2, $3, $4) RETURNING task_id""",
                    user_id, encrypted_text, category, deadline
                )
                logger.info(f"Created encrypted task {task_id} for user {user_id}")
                return task_id
        
        return await self.safe_execute(_create_task)
    
    async def get_user_tasks(self, user_id: int, status: str = None) -> List[Dict[str, Any]]:
        """Получение задач пользователя с расшифровкой"""
        async def _get_tasks():
            base_query = """SELECT task_id, text, category, deadline, status, created_at, completed_at, marked_overdue_at
                           FROM tasks WHERE user_id = $1"""
            
            async with self.pool.acquire() as conn:
                if status:
                    tasks = await conn.fetch(f"{base_query} AND status = $2 ORDER BY created_at DESC", user_id, status)
                else:
                    tasks = await conn.fetch(f"{base_query} ORDER BY created_at DESC", user_id)
            
            return self._decrypt_items(tasks, 'text', 'task_id', 'task')
        
        return await self.safe_execute(_get_tasks)
    
    async def update_task_status(self, task_id: int, user_id: int, status: str) -> bool:
        """Обновление статуса задачи"""
        async def _update_status():
            async with self.pool.acquire() as conn:
                # ИСПРАВЛЕНИЕ: Используем транзакцию для атомарности
                async with conn.transaction():
                    if status in ('completed', 'failed'):
                        result = await conn.execute(
                            """UPDATE tasks 
                            SET status = $1::varchar, 
                                completed_at = NOW()
                            WHERE task_id = $2 AND user_id = $3""",
                            status, task_id, user_id
                        )
                    elif status == 'overdue':
                        result = await conn.execute(
                            """UPDATE tasks 
                            SET status = $1::varchar, 
                                marked_overdue_at = NOW()
                            WHERE task_id = $2 AND user_id = $3""",
                            status, task_id, user_id
                        )
                    else:
                        result = await conn.execute(
                            """UPDATE tasks 
                            SET status = $1::varchar
                            WHERE task_id = $2 AND user_id = $3""",
                            status, task_id, user_id
                        )
                    
                    return result != "UPDATE 0"
        
        return await self.safe_execute(_update_status)
    
    async def delete_task(self, task_id: int, user_id: int) -> bool:
        """Удаление задачи"""
        async def _delete_task():
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM tasks WHERE task_id = $1 AND user_id = $2",
                    task_id, user_id
                )
                return result != "DELETE 0"
        
        return await self.safe_execute(_delete_task)
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С НАПОМИНАНИЯМИ ===
    
    async def create_reminder(self, user_id: int, text: str, reminder_type: str, 
                            trigger_time=None, cron_expression: str = None, 
                            is_built_in: bool = False) -> int:
        """Создание нового напоминания с шифрованием"""
        async def _create_reminder():
            encrypted_text = self._encrypt_text(text)
            async with self.pool.acquire() as conn:
                reminder_id = await conn.fetchval(
                    """INSERT INTO reminders (user_id, text, reminder_type, trigger_time, cron_expression, is_built_in) 
                       VALUES ($1, $2, $3, $4, $5, $6) RETURNING reminder_id""",
                    user_id, encrypted_text, reminder_type, trigger_time, cron_expression, is_built_in
                )
                logger.info(f"Created encrypted reminder {reminder_id} for user {user_id}")
                return reminder_id
        
        return await self.safe_execute(_create_reminder)
    
    async def get_user_reminders(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение напоминаний пользователя с расшифровкой"""
        async def _get_reminders():
            async with self.pool.acquire() as conn:
                reminders = await conn.fetch(
                    """SELECT reminder_id, text, reminder_type, trigger_time, cron_expression, 
                              is_active, is_built_in, created_at 
                       FROM reminders 
                       WHERE user_id = $1 
                       ORDER BY created_at DESC""",
                    user_id
                )
            
            return self._decrypt_items(reminders, 'text', 'reminder_id', 'reminder')
        
        return await self.safe_execute(_get_reminders)
    
    async def get_active_reminders(self) -> List[Dict[str, Any]]:
        """Получение всех активных напоминаний для планировщика"""
        async def _get_active():
            async with self.pool.acquire() as conn:
                reminders = await conn.fetch(
                    """SELECT reminder_id, user_id, text, reminder_type, trigger_time, cron_expression
                       FROM reminders 
                       WHERE is_active = TRUE"""
                )
            
            return self._decrypt_items(reminders, 'text', 'reminder_id', 'reminder')
        
        return await self.safe_execute(_get_active)
    
    async def update_reminder_status(self, reminder_id: int, is_active: bool) -> bool:
        """Обновление статуса напоминания"""
        async def _update_reminder():
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE reminders SET is_active = $1 WHERE reminder_id = $2",
                    is_active, reminder_id
                )
                return result != "UPDATE 0"
        
        return await self.safe_execute(_update_reminder)
    
    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        """Удаление напоминания"""
        async def _delete_reminder():
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM reminders WHERE reminder_id = $1 AND user_id = $2",
                    reminder_id, user_id
                )
                return result != "DELETE 0"
        
        return await self.safe_execute(_delete_reminder)
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С ДНЕВНИКОМ ===
    
    async def create_diary_entry(self, user_id: int, entry_date, content: str) -> int:
        """Создание новой записи дневника с шифрованием"""
        async def _create_entry():
            encrypted_content = self._encrypt_text(content)
            async with self.pool.acquire() as conn:
                entry_id = await conn.fetchval(
                    """INSERT INTO diary_entries (user_id, entry_date, content) 
                       VALUES ($1, $2, $3) RETURNING entry_id""",
                    user_id, entry_date, encrypted_content
                )
                logger.info(f"Created encrypted diary entry {entry_id} for user {user_id}")
                return entry_id
        
        return await self.safe_execute(_create_entry)
    
    async def get_diary_entries_by_date(self, user_id: int, entry_date) -> List[Dict[str, Any]]:
        """Получение записей дневника за определенную дату с расшифровкой"""
        async def _get_entries():
            async with self.pool.acquire() as conn:
                entries = await conn.fetch(
                    """SELECT entry_id, content, created_at, is_edited 
                       FROM diary_entries 
                       WHERE user_id = $1 AND entry_date = $2 
                       ORDER BY created_at ASC""",
                    user_id, entry_date
                )
            
            return self._decrypt_items(entries, 'content', 'entry_id', 'diary entry')
        
        return await self.safe_execute(_get_entries)
    
    async def get_diary_entries_by_period(self, user_id: int, start_date, end_date) -> List[Dict[str, Any]]:
        """Получение записей дневника за период с расшифровкой"""
        async def _get_period_entries():
            async with self.pool.acquire() as conn:
                entries = await conn.fetch(
                    """SELECT entry_date, content, created_at, is_edited 
                       FROM diary_entries 
                       WHERE user_id = $1 AND entry_date BETWEEN $2 AND $3 
                       ORDER BY entry_date DESC, created_at ASC""",
                    user_id, start_date, end_date
                )
            
            # Здесь нет entry_id, поэтому используем другой подход
            decrypted_entries = []
            for entry in entries:
                entry_dict = dict(entry)
                entry_dict['content'] = self._decrypt_text(entry['content'], item_type="diary entry")
                decrypted_entries.append(entry_dict)
            
            return decrypted_entries
        
        return await self.safe_execute(_get_period_entries)
    
    async def update_diary_entry(self, entry_id: int, user_id: int, content: str) -> bool:
        """Обновление записи дневника с шифрованием"""
        async def _update_entry():
            encrypted_content = self._encrypt_text(content)
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """UPDATE diary_entries 
                       SET content = $1, updated_at = NOW(), is_edited = TRUE 
                       WHERE entry_id = $2 AND user_id = $3""",
                    encrypted_content, entry_id, user_id
                )
                return result != "UPDATE 0"
        
        return await self.safe_execute(_update_entry)
    
    async def delete_diary_entry(self, entry_id: int, user_id: int) -> bool:
        """Удаление записи дневника"""
        async def _delete_entry():
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM diary_entries WHERE entry_id = $1 AND user_id = $2",
                    entry_id, user_id
                )
                return result != "DELETE 0"
        
        return await self.safe_execute(_delete_entry)
    
    async def get_diary_entry_date(self, entry_id: int) -> Optional[Any]:
        """Получение даты записи дневника"""
        async def _get_date():
            async with self.pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT entry_date FROM diary_entries WHERE entry_id = $1",
                    entry_id
                )
        
        return await self.safe_execute(_get_date)
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С КАТЕГОРИЯМИ ===
    
    async def get_task_categories(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение категорий задач пользователя"""
        async def _get_categories():
            async with self.pool.acquire() as conn:
                categories = await conn.fetch(
                    """SELECT category_id, name, created_at 
                       FROM task_categories 
                       WHERE user_id = $1 
                       ORDER BY created_at DESC""",
                    user_id
                )
            
            return [dict(category) for category in categories]
        
        return await self.safe_execute(_get_categories)
    
    async def create_task_category(self, user_id: int, name: str) -> Optional[int]:
        """Создание новой категории задач"""
        async def _create_category():
            async with self.pool.acquire() as conn:
                try:
                    category_id = await conn.fetchval(
                        """INSERT INTO task_categories (user_id, name) 
                           VALUES ($1, $2) RETURNING category_id""",
                        user_id, name
                    )
                    return category_id
                except Exception as e:
                    logger.error(f"Failed to create category: {e}")
                    return None
        
        return await self.safe_execute(_create_category)
    
    # === МИГРАЦИЯ СУЩЕСТВУЮЩИХ ДАННЫХ ===
    
    async def _migrate_table_data(self, conn, table: str, id_field: str, text_field: str):
        """Универсальный метод для миграции данных таблицы"""
        items = await conn.fetch(f"SELECT {id_field}, {text_field} FROM {table}")
        
        for item in items:
            try:
                # Проверяем, не зашифровано ли уже
                try:
                    self._decrypt_text(item[text_field])
                    continue  # Уже зашифровано
                except:
                    pass  # Не зашифровано, нужно зашифровать
                
                encrypted_text = self._encrypt_text(item[text_field])
                await conn.execute(
                    f"UPDATE {table} SET {text_field} = $1 WHERE {id_field} = $2",
                    encrypted_text, item[id_field]
                )
                logger.info(f"Migrated {table} {item[id_field]}")
            except Exception as e:
                logger.error(f"Failed to migrate {table} {item[id_field]}: {e}")
    
    async def migrate_existing_data_to_encrypted(self):
        """Миграция существующих незашифрованных данных"""
        async def _migrate():
            logger.info("Starting data migration to encrypted format...")
            
            async with self.pool.acquire() as conn:
                # Миграция всех таблиц с зашифрованными данными
                migrations = [
                    ("tasks", "task_id", "text"),
                    ("reminders", "reminder_id", "text"),
                    ("diary_entries", "entry_id", "content")
                ]
                
                for table, id_field, text_field in migrations:
                    await self._migrate_table_data(conn, table, id_field, text_field)
            
            logger.info("Data migration to encrypted format completed")
        
        return await self.safe_execute(_migrate)

# Глобальный экземпляр базы данных
db = Database()
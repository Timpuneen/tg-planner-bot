import asyncpg
from typing import Optional, List, Dict, Any
from config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        """Создание пула соединений с базой данных"""
        try:
            self.pool = await asyncpg.create_pool(DATABASE_URL)
            await self.create_tables()
            logger.info("Database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    async def disconnect(self):
        """Закрытие пула соединений"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection closed")
    
    async def create_tables(self):
        """Создание таблиц в базе данных"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    timezone VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Таблица напоминаний
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    text TEXT NOT NULL,
                    reminder_type VARCHAR(20) NOT NULL,
                    trigger_time TIMESTAMP,
                    cron_expression VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    is_built_in BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Таблица задач
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    text TEXT NOT NULL,
                    category VARCHAR(100),
                    deadline TIMESTAMP,
                    status VARCHAR(20) DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP,
                    marked_overdue_at TIMESTAMP
                )
            """)

            # Таблица для категорий задач
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS task_categories (
                    category_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    name VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, name)
                )
            """)

            # Обновленная таблица записей дневника
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS diary_entries (
                    entry_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    entry_date DATE NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    is_edited BOOLEAN DEFAULT FALSE
                )
            """)

            # Добавляем индексы для оптимизации
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_user_status 
                ON tasks(user_id, status)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_deadline 
                ON tasks(deadline) WHERE deadline IS NOT NULL
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_categories_user 
                ON task_categories(user_id, created_at DESC)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_diary_entries_user_date 
                ON diary_entries(user_id, entry_date DESC)
            """)
        
        logger.info("Database tables created/verified")
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С ЗАДАЧАМИ ===
    
    async def create_task(self, user_id: int, text: str, category: str = None, deadline=None) -> int:
        """Создание новой задачи с шифрованием"""
        from services.encryption_service import encrypt_text
        
        encrypted_text = encrypt_text(text)
        
        async with self.pool.acquire() as conn:
            task_id = await conn.fetchval(
                """INSERT INTO tasks (user_id, text, category, deadline) 
                   VALUES ($1, $2, $3, $4) RETURNING task_id""",
                user_id, encrypted_text, category, deadline
            )
            logger.info(f"Created encrypted task {task_id} for user {user_id}")
            return task_id
    
    async def get_user_tasks(self, user_id: int, status: str = None) -> List[Dict[str, Any]]:
        """Получение задач пользователя с расшифровкой"""
        from services.encryption_service import decrypt_text
        
        async with self.pool.acquire() as conn:
            if status:
                tasks = await conn.fetch(
                    """SELECT task_id, text, category, deadline, status, created_at, completed_at, marked_overdue_at
                       FROM tasks WHERE user_id = $1 AND status = $2 
                       ORDER BY created_at DESC""",
                    user_id, status
                )
            else:
                tasks = await conn.fetch(
                    """SELECT task_id, text, category, deadline, status, created_at, completed_at, marked_overdue_at
                       FROM tasks WHERE user_id = $1 
                       ORDER BY created_at DESC""",
                    user_id
                )
        
        # Расшифровываем текст задач
        decrypted_tasks = []
        for task in tasks:
            task_dict = dict(task)
            try:
                task_dict['text'] = decrypt_text(task['text'])
            except Exception as e:
                logger.error(f"Failed to decrypt task {task['task_id']}: {e}")
                task_dict['text'] = "[Ошибка расшифровки]"
            decrypted_tasks.append(task_dict)
        
        return decrypted_tasks
    
    async def update_task_status(self, task_id: int, user_id: int, status: str) -> bool:
        """Обновление статуса задачи"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE tasks SET status = $1, 
                   completed_at = CASE WHEN $1 = 'completed' THEN NOW() ELSE completed_at END,
                   marked_overdue_at = CASE WHEN $1 = 'overdue' THEN NOW() ELSE marked_overdue_at END
                   WHERE task_id = $2 AND user_id = $3""",
                status, task_id, user_id
            )
            return result != "UPDATE 0"
    
    async def delete_task(self, task_id: int, user_id: int) -> bool:
        """Удаление задачи"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM tasks WHERE task_id = $1 AND user_id = $2",
                task_id, user_id
            )
            return result != "DELETE 0"
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С НАПОМИНАНИЯМИ ===
    
    async def create_reminder(self, user_id: int, text: str, reminder_type: str, 
                            trigger_time=None, cron_expression: str = None, 
                            is_built_in: bool = False) -> int:
        """Создание нового напоминания с шифрованием"""
        from services.encryption_service import encrypt_text
        
        encrypted_text = encrypt_text(text)
        
        async with self.pool.acquire() as conn:
            reminder_id = await conn.fetchval(
                """INSERT INTO reminders (user_id, text, reminder_type, trigger_time, cron_expression, is_built_in) 
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING reminder_id""",
                user_id, encrypted_text, reminder_type, trigger_time, cron_expression, is_built_in
            )
            logger.info(f"Created encrypted reminder {reminder_id} for user {user_id}")
            return reminder_id
    
    async def get_user_reminders(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение напоминаний пользователя с расшифровкой"""
        from services.encryption_service import decrypt_text
        
        async with self.pool.acquire() as conn:
            reminders = await conn.fetch(
                """SELECT reminder_id, text, reminder_type, trigger_time, cron_expression, 
                          is_active, is_built_in, created_at 
                   FROM reminders 
                   WHERE user_id = $1 
                   ORDER BY created_at DESC""",
                user_id
            )
        
        # Расшифровываем текст напоминаний
        decrypted_reminders = []
        for reminder in reminders:
            reminder_dict = dict(reminder)
            try:
                reminder_dict['text'] = decrypt_text(reminder['text'])
            except Exception as e:
                logger.error(f"Failed to decrypt reminder {reminder['reminder_id']}: {e}")
                reminder_dict['text'] = "[Ошибка расшифровки]"
            decrypted_reminders.append(reminder_dict)
        
        return decrypted_reminders
    
    async def get_active_reminders(self) -> List[Dict[str, Any]]:
        """Получение всех активных напоминаний для планировщика"""
        from services.encryption_service import decrypt_text
        
        async with self.pool.acquire() as conn:
            reminders = await conn.fetch(
                """SELECT reminder_id, user_id, text, reminder_type, trigger_time, cron_expression
                   FROM reminders 
                   WHERE is_active = TRUE"""
            )
        
        # Расшифровываем текст напоминаний
        decrypted_reminders = []
        for reminder in reminders:
            reminder_dict = dict(reminder)
            try:
                reminder_dict['text'] = decrypt_text(reminder['text'])
            except Exception as e:
                logger.error(f"Failed to decrypt reminder {reminder['reminder_id']}: {e}")
                reminder_dict['text'] = "[Ошибка расшифровки]"
            decrypted_reminders.append(reminder_dict)
        
        return decrypted_reminders
    
    async def update_reminder_status(self, reminder_id: int, is_active: bool) -> bool:
        """Обновление статуса напоминания"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE reminders SET is_active = $1 WHERE reminder_id = $2",
                is_active, reminder_id
            )
            return result != "UPDATE 0"
    
    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        """Удаление напоминания"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM reminders WHERE reminder_id = $1 AND user_id = $2",
                reminder_id, user_id
            )
            return result != "DELETE 0"
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С ДНЕВНИКОМ ===
    
    async def create_diary_entry(self, user_id: int, entry_date, content: str) -> int:
        """Создание новой записи дневника с шифрованием"""
        from services.encryption_service import encrypt_text
        
        encrypted_content = encrypt_text(content)
        
        async with self.pool.acquire() as conn:
            entry_id = await conn.fetchval(
                """INSERT INTO diary_entries (user_id, entry_date, content) 
                   VALUES ($1, $2, $3) RETURNING entry_id""",
                user_id, entry_date, encrypted_content
            )
            logger.info(f"Created encrypted diary entry {entry_id} for user {user_id}")
            return entry_id
    
    async def get_diary_entries_by_date(self, user_id: int, entry_date) -> List[Dict[str, Any]]:
        """Получение записей дневника за определенную дату с расшифровкой"""
        from services.encryption_service import decrypt_text
        
        async with self.pool.acquire() as conn:
            entries = await conn.fetch(
                """SELECT entry_id, content, created_at, is_edited 
                   FROM diary_entries 
                   WHERE user_id = $1 AND entry_date = $2 
                   ORDER BY created_at ASC""",
                user_id, entry_date
            )
        
        # Расшифровываем содержимое записей
        decrypted_entries = []
        for entry in entries:
            entry_dict = dict(entry)
            try:
                entry_dict['content'] = decrypt_text(entry['content'])
            except Exception as e:
                logger.error(f"Failed to decrypt diary entry {entry['entry_id']}: {e}")
                entry_dict['content'] = "[Ошибка расшифровки]"
            decrypted_entries.append(entry_dict)
        
        return decrypted_entries
    
    async def get_diary_entries_by_period(self, user_id: int, start_date, end_date) -> List[Dict[str, Any]]:
        """Получение записей дневника за период с расшифровкой"""
        from services.encryption_service import decrypt_text
        
        async with self.pool.acquire() as conn:
            entries = await conn.fetch(
                """SELECT entry_date, content, created_at, is_edited 
                   FROM diary_entries 
                   WHERE user_id = $1 AND entry_date BETWEEN $2 AND $3 
                   ORDER BY entry_date DESC, created_at ASC""",
                user_id, start_date, end_date
            )
        
        # Расшифровываем содержимое записей
        decrypted_entries = []
        for entry in entries:
            entry_dict = dict(entry)
            try:
                entry_dict['content'] = decrypt_text(entry['content'])
            except Exception as e:
                logger.error(f"Failed to decrypt diary entry: {e}")
                entry_dict['content'] = "[Ошибка расшифровки]"
            decrypted_entries.append(entry_dict)
        
        return decrypted_entries
    
    async def update_diary_entry(self, entry_id: int, user_id: int, content: str) -> bool:
        """Обновление записи дневника с шифрованием"""
        from services.encryption_service import encrypt_text
        
        encrypted_content = encrypt_text(content)
        
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE diary_entries 
                   SET content = $1, updated_at = NOW(), is_edited = TRUE 
                   WHERE entry_id = $2 AND user_id = $3""",
                encrypted_content, entry_id, user_id
            )
            return result != "UPDATE 0"
    
    async def delete_diary_entry(self, entry_id: int, user_id: int) -> bool:
        """Удаление записи дневника"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM diary_entries WHERE entry_id = $1 AND user_id = $2",
                entry_id, user_id
            )
            return result != "DELETE 0"
    
    async def get_diary_entry_date(self, entry_id: int) -> Optional[Any]:
        """Получение даты записи дневника"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT entry_date FROM diary_entries WHERE entry_id = $1",
                entry_id
            )
    
    # === МЕТОДЫ ДЛЯ РАБОТЫ С КАТЕГОРИЯМИ ===
    
    async def get_task_categories(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение категорий задач пользователя"""
        async with self.pool.acquire() as conn:
            categories = await conn.fetch(
                """SELECT category_id, name, created_at 
                   FROM task_categories 
                   WHERE user_id = $1 
                   ORDER BY created_at DESC""",
                user_id
            )
        
        return [dict(category) for category in categories]
    
    async def create_task_category(self, user_id: int, name: str) -> Optional[int]:
        """Создание новой категории задач"""
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
    
    # === МИГРАЦИЯ СУЩЕСТВУЮЩИХ ДАННЫХ ===
    
    async def migrate_existing_data_to_encrypted(self):
        """Миграция существующих незашифрованных данных"""
        from services.encryption_service import encrypt_text
        
        logger.info("Starting data migration to encrypted format...")
        
        async with self.pool.acquire() as conn:
            # Миграция задач
            tasks = await conn.fetch("SELECT task_id, text FROM tasks")
            for task in tasks:
                try:
                    # Проверяем, не зашифрован ли уже текст
                    # Если текст можно расшифровать, значит он уже зашифрован
                    from services.encryption_service import decrypt_text
                    try:
                        decrypt_text(task['text'])
                        continue  # Уже зашифровано
                    except:
                        # Не зашифровано, нужно зашифровать
                        pass
                    
                    encrypted_text = encrypt_text(task['text'])
                    await conn.execute(
                        "UPDATE tasks SET text = $1 WHERE task_id = $2",
                        encrypted_text, task['task_id']
                    )
                    logger.info(f"Migrated task {task['task_id']}")
                except Exception as e:
                    logger.error(f"Failed to migrate task {task['task_id']}: {e}")
            
            # Миграция напоминаний
            reminders = await conn.fetch("SELECT reminder_id, text FROM reminders")
            for reminder in reminders:
                try:
                    # Проверяем, не зашифровано ли уже
                    from services.encryption_service import decrypt_text
                    try:
                        decrypt_text(reminder['text'])
                        continue  # Уже зашифровано
                    except:
                        # Не зашифровано, нужно зашифровать
                        pass
                    
                    encrypted_text = encrypt_text(reminder['text'])
                    await conn.execute(
                        "UPDATE reminders SET text = $1 WHERE reminder_id = $2",
                        encrypted_text, reminder['reminder_id']
                    )
                    logger.info(f"Migrated reminder {reminder['reminder_id']}")
                except Exception as e:
                    logger.error(f"Failed to migrate reminder {reminder['reminder_id']}: {e}")
            
            # Миграция записей дневника
            diary_entries = await conn.fetch("SELECT entry_id, content FROM diary_entries")
            for entry in diary_entries:
                try:
                    # Проверяем, не зашифровано ли уже
                    from services.encryption_service import decrypt_text
                    try:
                        decrypt_text(entry['content'])
                        continue  # Уже зашифровано
                    except:
                        # Не зашифровано, нужно зашифровать
                        pass
                    
                    encrypted_content = encrypt_text(entry['content'])
                    await conn.execute(
                        "UPDATE diary_entries SET content = $1 WHERE entry_id = $2",
                        encrypted_content, entry['entry_id']
                    )
                    logger.info(f"Migrated diary entry {entry['entry_id']}")
                except Exception as e:
                    logger.error(f"Failed to migrate diary entry {entry['entry_id']}: {e}")
        
        logger.info("Data migration to encrypted format completed")

# Глобальный экземпляр базы данных
db = Database()
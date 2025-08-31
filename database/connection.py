import asyncpg
from typing import Optional
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

# Глобальный экземпляр базы данных
db = Database()
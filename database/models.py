from datetime import datetime
from typing import Optional

class User:
    def __init__(self, user_id: int, timezone: str, created_at: datetime = None):
        self.user_id = user_id
        self.timezone = timezone
        self.created_at = created_at or datetime.now()

class Reminder:
    def __init__(self, reminder_id: int = None, user_id: int = None, text: str = None, 
                 reminder_type: str = None, trigger_time: datetime = None, 
                 cron_expression: str = None, is_active: bool = True, 
                 is_built_in: bool = False, created_at: datetime = None):
        self.reminder_id = reminder_id
        self.user_id = user_id
        self.text = text
        self.reminder_type = reminder_type  # 'once' или 'recurring'
        self.trigger_time = trigger_time
        self.cron_expression = cron_expression
        self.is_active = is_active
        self.is_built_in = is_built_in
        self.created_at = created_at or datetime.now()

class Task:
    def __init__(self, task_id: int = None, user_id: int = None, text: str = None,
                 category: str = None, deadline: datetime = None,
                 status: str = 'active', created_at: datetime = None,
                 completed_at: datetime = None, marked_overdue_at: datetime = None):
        self.task_id = task_id
        self.user_id = user_id
        self.text = text
        self.category = category
        self.deadline = deadline
        self.status = status  # 'active', 'completed', 'failed', 'overdue'
        self.created_at = created_at or datetime.now()
        self.completed_at = completed_at
        self.marked_overdue_at = marked_overdue_at

class TaskCategory:
    def __init__(self, category_id: int = None, user_id: int = None, name: str = None,
                 created_at: datetime = None):
        self.category_id = category_id
        self.user_id = user_id
        self.name = name
        self.created_at = created_at or datetime.now()

class DiaryEntry:
    def __init__(self, entry_id: int = None, user_id: int = None, 
                 entry_date: datetime = None, content: str = None,
                 created_at: datetime = None, updated_at: datetime = None,
                 is_edited: bool = False):
        self.entry_id = entry_id
        self.user_id = user_id
        self.entry_date = entry_date
        self.content = content
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at
        self.is_edited = is_edited
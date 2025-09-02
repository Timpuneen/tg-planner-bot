from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from config import POPULAR_TIMEZONES
from typing import List, Tuple, Optional

def get_timezone_keyboard():
    """Клавиатура для выбора часового пояса"""
    keyboard = []
    for tz_code, tz_name in POPULAR_TIMEZONES:
        keyboard.append([InlineKeyboardButton(text=tz_name, callback_data=f"tz_{tz_code}")])
    
    keyboard.append([InlineKeyboardButton(text="📍 Отправить геолокацию", callback_data="send_location")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_main_menu_keyboard():
    """Главное меню (custom keyboard)"""
    keyboard = [
        [KeyboardButton(text="⏰ Напоминания")],
        [KeyboardButton(text="📝 Задачи")],
        [KeyboardButton(text="📔 Дневник")],
        [KeyboardButton(text="🌍 Изменить часовой пояс")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_reminders_menu_keyboard():
    """Меню напоминаний"""
    keyboard = [
        [KeyboardButton(text="➕ Разовое напоминание")],
        [KeyboardButton(text="🔄 Повторяющееся напоминание")],
        [KeyboardButton(text="📋 Список напоминаний")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_tasks_menu_keyboard():
    """Меню задач"""
    keyboard = [
        [KeyboardButton(text="➕ Создать новую задачу")],
        [KeyboardButton(text="👀 Просмотр задач")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_task_view_menu_keyboard():
    """Меню просмотра задач"""
    keyboard = [
        [KeyboardButton(text="🔥 Активные")],
        [KeyboardButton(text="✅ Выполненные")],
        [KeyboardButton(text="❌ Невыполненные")],
        [KeyboardButton(text="⚠️ Просроченные")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_category_selection_keyboard(categories: List[Tuple[int, str]], max_display: int = 10):
    """Клавиатура для выбора категории задачи"""
    keyboard = []
    
    # Показываем последние использованные категории (максимум max_display)
    displayed_categories = categories[:max_display]
    for category_id, category_name in displayed_categories:
        keyboard.append([InlineKeyboardButton(
            text=category_name, 
            callback_data=f"category_select_{category_id}"
        )])
    
    # Опции управления
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Создать новую категорию", callback_data="category_new")],
        [InlineKeyboardButton(text="🚫 Без категории", callback_data="category_none")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_deadline_selection_keyboard():
    """Клавиатура для выбора дедлайна"""
    keyboard = [
        [InlineKeyboardButton(text="📅 До конца дня", callback_data="deadline_today")],
        [InlineKeyboardButton(text="📅 До конца завтрашнего дня", callback_data="deadline_tomorrow")],
        [InlineKeyboardButton(text="📆 До конца недели", callback_data="deadline_week")],
        [InlineKeyboardButton(text="🗓 До конца месяца", callback_data="deadline_month")],
        [InlineKeyboardButton(text="📊 До конца года", callback_data="deadline_year")],
        [InlineKeyboardButton(text="⏰ Свой дедлайн", callback_data="deadline_custom")],
        [InlineKeyboardButton(text="🚫 Без дедлайна", callback_data="deadline_none")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_task_action_keyboard(task_id: int, task_status: str):
    """Клавиатура действий с задачей"""
    keyboard = []
    
    if task_status == 'active':
        keyboard.extend([
            [InlineKeyboardButton(text="✅ Выполнена", callback_data=f"task_complete_{task_id}")],
            [InlineKeyboardButton(text="❌ Не выполнена", callback_data=f"task_fail_{task_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"task_delete_{task_id}")]
        ])
    elif task_status in ['completed', 'failed']:
        keyboard.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"task_delete_{task_id}")])
    elif task_status == 'overdue':
        keyboard.extend([
            [InlineKeyboardButton(text="⏰ Продлить", callback_data=f"task_extend_{task_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"task_delete_{task_id}")]
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_diary_menu_keyboard():
    """Меню дневника"""
    keyboard = [
        [KeyboardButton(text="✍️ Новая запись")],
        [KeyboardButton(text="📖 Просмотр записей")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_to_main_keyboard():
    """Кнопка возврата в главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Главное меню")]],
        resize_keyboard=True
    )
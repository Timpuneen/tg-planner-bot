from aiogram import Router, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import pytz
import logging

from database.connection import db
from keyboards.keyboards import (
    get_tasks_menu_keyboard, get_back_to_main_keyboard, get_task_view_menu_keyboard,
    get_category_selection_keyboard, get_deadline_selection_keyboard
)
from services.timezone_service import get_user_time

router = Router()
logger = logging.getLogger(__name__)

# Ограничения для задач
TASK_LIMITS = {
    'active': 50,
    'completed': 50,
    'failed': 25,
    'overdue': 25
}

class TaskStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_category = State()
    waiting_for_custom_category = State()
    waiting_for_deadline = State()
    waiting_for_custom_deadline = State()
    waiting_for_extend_deadline = State()

# ======= HELPER FUNCTIONS =======

def normalize_datetime_for_db(dt):
    """Нормализует datetime для сохранения в базу данных"""
    if dt is None:
        return None
    
    # Если datetime содержит timezone info, конвертируем в UTC и убираем timezone info
    if dt.tzinfo is not None:
        # Конвертируем в UTC и делаем naive
        utc_dt = dt.astimezone(pytz.UTC)
        return utc_dt.replace(tzinfo=None)
    
    # Если уже naive, возвращаем как есть
    return dt

def create_deadline_from_user_time(user_timezone_str, year, month, day, hour=23, minute=59, second=59):
    """Создает deadline с учетом часового пояса пользователя"""
    try:
        user_tz = pytz.timezone(user_timezone_str)
        # Создаем naive datetime
        naive_dt = datetime(year, month, day, hour, minute, second)
        # Локализуем в часовой пояс пользователя
        localized_dt = user_tz.localize(naive_dt)
        # Нормализуем для БД
        return normalize_datetime_for_db(localized_dt)
    except Exception as e:
        logger.error(f"Error creating deadline: {e}")
        return None

# ======= СОЗДАНИЕ ЗАДАЧ =======

@router.message(lambda message: message.text == "➕ Создать новую задачу")
async def create_task_start(message: types.Message, state: FSMContext):
    """Начало создания новой задачи"""
    # Проверяем лимит активных задач
    user_id = message.from_user.id
    
    async with db.pool.acquire() as conn:
        active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND status = 'active'",
            user_id
        )
    
    if active_count >= TASK_LIMITS['active']:
        await message.answer(
            f"⚠️ Достигнут лимит активных задач ({TASK_LIMITS['active']}).\n"
            "Завершите или удалите некоторые задачи перед созданием новых.",
            reply_markup=get_tasks_menu_keyboard()
        )
        return
    
    await message.answer(
        "📝 Создание новой задачи\n\n"
        "Введите текст задачи:",
        reply_markup=get_back_to_main_keyboard()
    )
    await state.set_state(TaskStates.waiting_for_text)

@router.message(TaskStates.waiting_for_text)
async def process_task_text(message: types.Message, state: FSMContext):
    """Обработка текста задачи"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    task_text = message.text.strip()
    if len(task_text) > 500:
        await message.answer(
            "❌ Текст задачи слишком длинный (максимум 500 символов).\n"
            "Попробуйте сократить:"
        )
        return
    
    # Логируем сохранение текста задачи (в зашифрованном виде он будет сохранен в БД)
    logger.info(f"Processing task text for user {message.from_user.id}")
    await state.update_data(task_text=task_text)
    
    # Проверяем, что данные сохранились
    data = await state.get_data()
    logger.debug(f"State data after saving text: task_text length = {len(data.get('task_text', ''))}")
    
    # Получаем последние 10 уникальных категорий пользователя
    user_id = message.from_user.id
    async with db.pool.acquire() as conn:
        categories = await conn.fetch(
            """SELECT DISTINCT category, MAX(created_at) as latest_created_at 
            FROM tasks 
            WHERE user_id = $1 AND category IS NOT NULL 
            GROUP BY category
            ORDER BY latest_created_at DESC LIMIT 10""",
            user_id
        )
    
    # Преобразуем в список для клавиатуры
    category_list = [(i, cat['category']) for i, cat in enumerate(categories)]
    keyboard = get_category_selection_keyboard(category_list)
    
    await message.answer(
        f"✅ Текст задачи: {task_text}\n\n"
        "Выберите категорию:",
        reply_markup=keyboard
    )
    await state.set_state(TaskStates.waiting_for_category)

@router.callback_query(lambda c: c.data.startswith("category_"))
async def process_category_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора категории"""
    action = callback.data.replace("category_", "")
    
    # Проверяем состояние перед обработкой категории
    data = await state.get_data()
    logger.debug(f"State data before category selection: task_text exists = {bool(data.get('task_text'))}")
    
    if not data.get("task_text"):
        logger.error("Task text is missing from state!")
        await callback.answer("❌ Ошибка: текст задачи потерян. Начните сначала.")
        await callback.message.answer(
            "Произошла ошибка. Пожалуйста, создайте задачу заново.",
            reply_markup=get_tasks_menu_keyboard()
        )
        await state.clear()
        return
    
    if action == "new":
        await callback.message.edit_text(
            "📝 Введите название новой категории:"
        )
        await state.set_state(TaskStates.waiting_for_custom_category)
        return
    elif action == "none":
        category = None
    else:
        # Получаем выбранную категорию
        user_id = callback.from_user.id
        async with db.pool.acquire() as conn:
            categories = await conn.fetch(
                """SELECT DISTINCT category, MAX(created_at) as latest_created_at 
                FROM tasks 
                WHERE user_id = $1 AND category IS NOT NULL 
                GROUP BY category
                ORDER BY latest_created_at DESC LIMIT 10""",
                user_id
            )
        
        try:
            category_index = int(action.split("_")[-1])
            category = categories[category_index]['category']
        except (ValueError, IndexError):
            await callback.answer("❌ Ошибка выбора категории")
            return
    
    await state.update_data(category=category)
    await show_deadline_selection(callback, state, category)

@router.message(TaskStates.waiting_for_custom_category)
async def process_custom_category(message: types.Message, state: FSMContext):
    """Обработка ввода новой категории"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    # Проверяем состояние
    data = await state.get_data()
    if not data.get("task_text"):
        logger.error("Task text is missing from state during custom category!")
        await message.answer(
            "❌ Ошибка: текст задачи потерян. Начните создание задачи заново.",
            reply_markup=get_tasks_menu_keyboard()
        )
        await state.clear()
        return
    
    category = message.text.strip()
    if len(category) > 100:
        await message.answer(
            "❌ Название категории слишком длинное (максимум 100 символов).\n"
            "Попробуйте еще раз:"
        )
        return
    
    await state.update_data(category=category)
    
    # Создаем фиктивный callback для передачи в функцию
    fake_callback = types.CallbackQuery(
        id="fake", from_user=message.from_user, chat_instance="fake", 
        message=message, data="fake"
    )
    
    await show_deadline_selection(fake_callback, state, category)

async def show_deadline_selection(callback, state, category):
    """Показать выбор дедлайна"""
    keyboard = get_deadline_selection_keyboard()
    
    category_text = f"Категория: {category}" if category else "Без категории"
    
    if hasattr(callback, 'message') and hasattr(callback.message, 'edit_text'):
        try:
            await callback.message.edit_text(
                f"✅ {category_text}\n\n"
                "Выберите дедлайн:",
                reply_markup=keyboard
            )
        except:
            # Если редактирование не удалось, отправляем новое сообщение
            await callback.message.answer(
                f"✅ {category_text}\n\n"
                "Выберите дедлайн:",
                reply_markup=keyboard
            )
    else:
        # Для обычного сообщения
        await callback.message.answer(
            f"✅ {category_text}\n\n"
            "Выберите дедлайн:",
            reply_markup=keyboard
        )
    
    await state.set_state(TaskStates.waiting_for_deadline)

@router.callback_query(lambda c: c.data.startswith("deadline_") and c.message.text.startswith("⏰ Выберите новый дедлайн"))
async def process_extend_deadline_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора дедлайна при продлении задачи"""
    action = callback.data.replace("deadline_", "")
    user_id = callback.from_user.id
    
    # Получаем часовой пояс пользователя
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    
    current_time = get_user_time(user['timezone'])
    new_deadline = None
    
    try:
        if action == "today":
            new_deadline = create_deadline_from_user_time(
                user['timezone'], 
                current_time.year, current_time.month, current_time.day
            )
        elif action == "week":
            days_until_sunday = 6 - current_time.weekday()
            target_date = current_time + timedelta(days=days_until_sunday)
            new_deadline = create_deadline_from_user_time(
                user['timezone'],
                target_date.year, target_date.month, target_date.day
            )
        elif action == "month":
            next_month = current_time.replace(day=28) + timedelta(days=4)
            last_day_of_month = next_month - timedelta(days=next_month.day)
            new_deadline = create_deadline_from_user_time(
                user['timezone'],
                last_day_of_month.year, last_day_of_month.month, last_day_of_month.day
            )
        elif action == "year":
            new_deadline = create_deadline_from_user_time(
                user['timezone'],
                current_time.year, 12, 31
            )
        elif action == "custom":
            await callback.message.edit_text(
                "📅 Введите новый дедлайн в формате ДД.ММ.ГГГГ\n"
                "Например: 15.12.2024"
            )
            await state.set_state(TaskStates.waiting_for_extend_deadline)
            return
        elif action == "none":
            new_deadline = None
    except Exception as e:
        logger.error(f"Error creating extend deadline: {e}")
        await callback.answer("❌ Ошибка при создании дедлайна")
        return
    
    # Обновляем задачу и возвращаемся к списку
    await complete_extend_task(callback, state, new_deadline)

@router.callback_query(lambda c: c.data.startswith("deadline_"))
async def process_deadline_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора дедлайна"""
    # Проверяем, не обрабатывается ли это расширение задачи
    if callback.message.text.startswith("⏰ Выберите новый дедлайн"):
        return  # Обработка в process_extend_deadline_callback
    
    # Проверяем состояние перед обработкой дедлайна
    data = await state.get_data()
    logger.debug(f"State data before deadline selection: task_text exists = {bool(data.get('task_text'))}")
    
    if not data.get("task_text"):
        logger.error("Task text is missing from state during deadline selection!")
        await callback.answer("❌ Ошибка: текст задачи потерян. Начните сначала.")
        await callback.message.answer(
            "Произошла ошибка. Пожалуйста, создайте задачу заново.",
            reply_markup=get_tasks_menu_keyboard()
        )
        await state.clear()
        return
    
    action = callback.data.replace("deadline_", "")
    user_id = callback.from_user.id
    
    # Получаем часовой пояс пользователя
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    
    user_timezone = user['timezone']
    current_time = get_user_time(user_timezone)
    deadline = None
    
    try:
        if action == "today":
            deadline = create_deadline_from_user_time(
                user_timezone, 
                current_time.year, current_time.month, current_time.day
            )
        elif action == "week":
            # До воскресенья текущей недели
            days_until_sunday = 6 - current_time.weekday()
            target_date = current_time + timedelta(days=days_until_sunday)
            deadline = create_deadline_from_user_time(
                user_timezone,
                target_date.year, target_date.month, target_date.day
            )
        elif action == "month":
            # До конца текущего месяца
            next_month = current_time.replace(day=28) + timedelta(days=4)
            last_day_of_month = next_month - timedelta(days=next_month.day)
            deadline = create_deadline_from_user_time(
                user_timezone,
                last_day_of_month.year, last_day_of_month.month, last_day_of_month.day
            )
        elif action == "year":
            # До конца текущего года
            deadline = create_deadline_from_user_time(
                user_timezone,
                current_time.year, 12, 31
            )
        elif action == "custom":
            await callback.message.edit_text(
                "📅 Введите дедлайн в формате ДД.ММ.ГГГГ\n"
                "Например: 15.12.2024"
            )
            await state.set_state(TaskStates.waiting_for_custom_deadline)
            return
        elif action == "none":
            deadline = None
    except Exception as e:
        logger.error(f"Error creating deadline: {e}")
        await callback.answer("❌ Ошибка при создании дедлайна")
        return
    
    await save_task(callback, state, deadline)

@router.message(TaskStates.waiting_for_custom_deadline)
async def process_custom_deadline(message: types.Message, state: FSMContext):
    """Обработка ввода произвольного дедлайна"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    # Проверяем состояние
    data = await state.get_data()
    if not data.get("task_text"):
        logger.error("Task text is missing from state during custom deadline!")
        await message.answer(
            "❌ Ошибка: текст задачи потерян. Начните создание задачи заново.",
            reply_markup=get_tasks_menu_keyboard()
        )
        await state.clear()
        return
    
    try:
        # Парсим дату в формате ДД.ММ.ГГГГ
        date_parts = message.text.strip().split('.')
        if len(date_parts) != 3:
            raise ValueError("Неверный формат")
        
        day, month, year = map(int, date_parts)
        
        # Получаем часовой пояс пользователя
        user_id = message.from_user.id
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
        
        current_time = get_user_time(user['timezone'])
        
        # Создаем дедлайн с правильной обработкой часового пояса
        deadline = create_deadline_from_user_time(
            user['timezone'], year, month, day
        )
        
        if deadline is None:
            raise ValueError("Ошибка создания дедлайна")
        
        # Проверяем, что дата не в прошлом (сравниваем в UTC)
        current_utc = normalize_datetime_for_db(current_time)
        if deadline < current_utc:
            await message.answer(
                "❌ Нельзя устанавливать дедлайн на прошедшую дату.\n"
                "Попробуйте еще раз:"
            )
            return
        
        # Создаем фиктивный callback для передачи в функцию
        fake_callback = types.CallbackQuery(
            id="fake", from_user=message.from_user, chat_instance="fake", 
            message=message, data="fake"
        )
        
        await save_task(fake_callback, state, deadline)
        
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing custom deadline: {e}")
        await message.answer(
            "❌ Неверный формат даты!\n"
            "Используйте формат ДД.ММ.ГГГГ\n"
            "Например: 15.12.2024\n\n"
            "Попробуйте еще раз:"
        )

async def save_task(callback, state, deadline):
    """Сохранение задачи в базу данных"""
    try:
        data = await state.get_data()
        task_text = data.get("task_text")
        category = data.get("category")
        user_id = callback.from_user.id
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: убеждаемся, что task_text не None
        if not task_text:
            logger.error(f"Critical error: task_text is None or empty! Data keys: {list(data.keys())}")
            await callback.message.answer(
                "❌ Критическая ошибка: текст задачи отсутствует. Начните создание заново.",
                reply_markup=get_tasks_menu_keyboard()
            )
            await state.clear()
            return
        
        logger.info(f"Saving task for user {user_id}: category='{category}', has_deadline={deadline is not None}")
        
        # Нормализуем deadline для БД
        normalized_deadline = normalize_datetime_for_db(deadline)
        
        # Сохраняем задачу (текст будет автоматически зашифрован в db.create_task)
        task_id = await db.create_task(user_id, task_text, category, normalized_deadline)
        
        logger.info(f"Task saved successfully with ID: {task_id}")
        
        # Сохраняем категорию если она новая
        if category:
            async with db.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO task_categories (user_id, name) 
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    user_id, category
                )
        
        # Форматируем ответ
        if deadline:
            # Для отображения конвертируем обратно в пользовательский часовой пояс
            async with db.pool.acquire() as conn:
                user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
            
            display_deadline = pytz.UTC.localize(normalized_deadline).astimezone(pytz.timezone(user['timezone']))
            deadline_text = display_deadline.strftime('%d.%m.%Y')
        else:
            deadline_text = "без дедлайна"
        
        category_text = f"Категория: {category}" if category else "Без категории"
        
        success_message = (
            f"✅ Задача создана!\n\n"
            f"📝 {task_text}\n"
            f"📂 {category_text}\n"
            f"📅 Дедлайн: {deadline_text}"
        )
        
        if hasattr(callback, 'message') and hasattr(callback.message, 'edit_text'):
            try:
                await callback.message.edit_text(success_message)
            except:
                await callback.message.answer(success_message)
        else:
            await callback.message.answer(success_message)
        
        await callback.message.answer(
            "Что дальше?",
            reply_markup=get_tasks_menu_keyboard()
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error saving task: {e}", exc_info=True)
        await callback.message.answer(
            "❌ Ошибка при сохранении задачи. Попробуйте еще раз.",
            reply_markup=get_tasks_menu_keyboard()
        )
        await state.clear()

# ======= ОСТАЛЬНЫЕ ФУНКЦИИ (групповое отображение и т.д.) =======

def create_tasks_keyboard(tasks_data, status):
    """Создает клавиатуру для управления задачами с номерами"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = []
    
    for i, task in enumerate(tasks_data, 1):
        row = []
        task_id = task['task_id']
        
        # Кнопка удаления (всегда есть) с номером задачи
        row.append(InlineKeyboardButton(
            text=f"🗑#{i}",
            callback_data=f"group_delete_{task_id}_{status}"
        ))
        
        # Дополнительные кнопки в зависимости от статуса
        if status == 'active':
            row.append(InlineKeyboardButton(
                text=f"✅#{i}",
                callback_data=f"group_complete_{task_id}_{status}"
            ))
            row.append(InlineKeyboardButton(
                text=f"❌#{i}",
                callback_data=f"group_fail_{task_id}_{status}"
            ))
        elif status == 'overdue':
            row.append(InlineKeyboardButton(
                text=f"⏰#{i}",
                callback_data=f"group_extend_{task_id}_{status}"
            ))
            row.append(InlineKeyboardButton(
                text=f"✅#{i}",
                callback_data=f"group_complete_{task_id}_{status}"
            ))
            row.append(InlineKeyboardButton(
                text=f"❌#{i}",
                callback_data=f"group_fail_{task_id}_{status}"
            ))
        
        keyboard.append(row)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def format_tasks_message(tasks_data, title, user_timezone=None):
    """Форматирует сообщение со списком задач"""
    if not tasks_data:
        return f"{title}\n\nЗадач не найдено"
    
    message_parts = [title, ""]
    
    # Группируем по категориям
    categorized_tasks = {}
    uncategorized_tasks = []
    
    for task in tasks_data:
        if task['category']:
            if task['category'] not in categorized_tasks:
                categorized_tasks[task['category']] = []
            categorized_tasks[task['category']].append(task)
        else:
            uncategorized_tasks.append(task)
    
    task_counter = 1
    
    # Получаем текущую дату в часовом поясе пользователя для выделения задач на сегодня
    current_date = None
    if user_timezone:
        current_time = get_user_time(user_timezone)
        current_date = current_time.date()
    
    # Задачи по категориям
    for category_name, category_tasks in categorized_tasks.items():
        message_parts.append(f"📂 {category_name}")
        
        for task in category_tasks:
            deadline_text = ""
            task_prefix = ""
            
            if task.get('deadline'):
                # Конвертируем deadline в пользовательский часовой пояс
                if user_timezone:
                    deadline_display = pytz.UTC.localize(task['deadline']).astimezone(pytz.timezone(user_timezone))
                    deadline_date = deadline_display.date()
                    
                    # Выделяем задачи на сегодня
                    if current_date and deadline_date == current_date:
                        task_prefix = "🚨 "
                    
                    deadline_text = f" (📅 {deadline_display.strftime('%d.%m.%Y')})"
                else:
                    deadline_text = f" (📅 {task['deadline'].strftime('%d.%m.%Y')})"
            elif task.get('completed_at'):
                deadline_text = f" (📅 {task['completed_at'].strftime('%d.%m.%Y')})"
            
            task_text = f"{task_counter}. {task_prefix}{task['text']}{deadline_text}"
            message_parts.append(task_text)
            task_counter += 1
        
        message_parts.append("")
    
    # Задачи без категории
    if uncategorized_tasks:
        message_parts.append("📂 Без категории")
        
        for task in uncategorized_tasks:
            deadline_text = ""
            task_prefix = ""
            
            if task.get('deadline'):
                # Конвертируем deadline в пользовательский часовой пояс
                if user_timezone:
                    deadline_display = pytz.UTC.localize(task['deadline']).astimezone(pytz.timezone(user_timezone))
                    deadline_date = deadline_display.date()
                    
                    # Выделяем задачи на сегодня
                    if current_date and deadline_date == current_date:
                        task_prefix = "🚨 "
                    
                    deadline_text = f" (📅 {deadline_display.strftime('%d.%m.%Y')})"
                else:
                    deadline_text = f" (📅 {task['deadline'].strftime('%d.%m.%Y')})"
            elif task.get('completed_at'):
                deadline_text = f" (📅 {task['completed_at'].strftime('%d.%m.%Y')})"
            
            task_text = f"{task_counter}. {task_prefix}{task['text']}{deadline_text}"
            message_parts.append(task_text)
            task_counter += 1
    
    return "\n".join(message_parts)

async def send_tasks_group_message(message: types.Message, status: str, title: str):
    """Отправляет сообщение с группой задач и кнопками управления"""
    user_id = message.from_user.id
    
    async with db.pool.acquire() as conn:
        # Получаем часовой пояс пользователя
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
        user_timezone = user['timezone'] if user else None
    
    # Получаем задачи (уже расшифрованные из db)
    tasks = await db.get_user_tasks(user_id, status)
    
    if not tasks:
        await message.answer(f"{title}\n\nЗадач не найдено")
        return
    
    # Форматируем сообщение с учетом часового пояса
    message_text = format_tasks_message(tasks, title, user_timezone)
    
    # Создаем клавиатуру
    keyboard = create_tasks_keyboard(tasks, status)
    
    # Отправляем сообщение
    await message.answer(message_text, reply_markup=keyboard)

# ======= АВТООБНОВЛЕНИЕ СТАТУСОВ =======

async def update_overdue_tasks_for_user(user_id: int):
    """Обновление просроченных задач для конкретного пользователя"""
    try:
        async with db.pool.acquire() as conn:
            # Получаем часовой пояс пользователя
            user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
            if not user:
                return
            
            current_time = get_user_time(user['timezone'])
            current_utc = normalize_datetime_for_db(current_time)
            
            # Находим задачи, которые должны стать просроченными
            overdue_tasks = await conn.fetch(
                """SELECT task_id FROM tasks 
                   WHERE user_id = $1 AND status = 'active' 
                   AND deadline IS NOT NULL AND deadline < $2""",
                user_id, current_utc
            )
            
            if overdue_tasks:
                # Проверяем лимит просроченных задач
                overdue_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND status = 'overdue'",
                    user_id
                )
                
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
                        user_id, delete_count
                    )
                
                # Помечаем задачи как просроченные
                task_ids = [task['task_id'] for task in overdue_tasks]
                await conn.execute(
                    """UPDATE tasks SET status = 'overdue', marked_overdue_at = NOW() 
                       WHERE task_id = ANY($1::int[])""",
                    task_ids
                )
                
                logger.info(f"Auto-marked {len(task_ids)} tasks as overdue for user {user_id}")
    
    except Exception as e:
        logger.error(f"Error updating overdue tasks for user {user_id}: {e}")

# ======= ПРОСМОТР ЗАДАЧ =======

@router.message(lambda message: message.text == "👀 Просмотр задач")
async def view_tasks_menu(message: types.Message):
    """Меню просмотра задач с автообновлением статусов"""
    # Сначала обновляем просроченные задачи
    await update_overdue_tasks_for_user(message.from_user.id)
    
    await message.answer(
        "👀 Просмотр задач\n\n"
        "Выберите категорию:",
        reply_markup=get_task_view_menu_keyboard()
    )

@router.message(lambda message: message.text == "🔥 Активные")
async def view_active_tasks(message: types.Message):
    """Просмотр активных задач"""
    # Обновляем статусы перед показом
    await update_overdue_tasks_for_user(message.from_user.id)
    await send_tasks_group_message(message, 'active', "🔥 Активные задачи")

@router.message(lambda message: message.text == "✅ Выполненные")
async def view_completed_tasks(message: types.Message):
    """Просмотр выполненных задач"""
    await send_tasks_group_message(message, 'completed', "✅ Выполненные задачи")

@router.message(lambda message: message.text == "❌ Невыполненные")
async def view_failed_tasks(message: types.Message):
    """Просмотр невыполненных задач"""
    await send_tasks_group_message(message, 'failed', "❌ Невыполненные задачи")

@router.message(lambda message: message.text == "⚠️ Просроченные")
async def view_overdue_tasks(message: types.Message):
    """Просмотр просроченных задач"""
    # Обновляем статусы перед показом
    await update_overdue_tasks_for_user(message.from_user.id)
    await send_tasks_group_message(message, 'overdue', "⚠️ Просроченные задачи")

# ======= ОБРАБОТЧИКИ ГРУППОВЫХ ДЕЙСТВИЙ =======

@router.callback_query(lambda c: c.data.startswith("group_complete_"))
async def group_complete_task(callback: types.CallbackQuery):
    """Отметить задачу как выполненную в групповом режиме"""
    parts = callback.data.split("_")
    task_id = int(parts[2])
    current_status = parts[3]
    user_id = callback.from_user.id
    
    # Используем методы из db для обновления статуса
    success = await db.update_task_status(task_id, user_id, 'completed')
    
    if success:
        # Проверяем лимит выполненных задач и принудительно применяем его
        await enforce_task_limits(user_id, 'completed')
        await callback.answer("✅ Задача отмечена как выполненная!")
    else:
        await callback.answer("❌ Ошибка при обновлении задачи")
        return
    
    # Обновляем сообщение со списком задач
    await refresh_tasks_message(callback, current_status, user_id)

@router.callback_query(lambda c: c.data.startswith("group_fail_"))
async def group_fail_task(callback: types.CallbackQuery):
    """Отметить задачу как невыполненную в групповом режиме"""
    parts = callback.data.split("_")
    task_id = int(parts[2])
    current_status = parts[3]
    user_id = callback.from_user.id
    
    # Используем методы из db для обновления статуса
    success = await db.update_task_status(task_id, user_id, 'failed')
    
    if success:
        # Проверяем лимит невыполненных задач и принудительно применяем его
        await enforce_task_limits(user_id, 'failed')
        await callback.answer("❌ Задача отмечена как невыполненная")
    else:
        await callback.answer("❌ Ошибка при обновлении задачи")
        return
    
    # Обновляем сообщение со списком задач
    await refresh_tasks_message(callback, current_status, user_id)

@router.callback_query(lambda c: c.data.startswith("group_delete_"))
async def group_delete_task(callback: types.CallbackQuery):
    """Удалить задачу в групповом режиме"""
    parts = callback.data.split("_")
    task_id = int(parts[2])
    current_status = parts[3]
    user_id = callback.from_user.id
    
    # Используем методы из db для удаления
    success = await db.delete_task(task_id, user_id)
    
    if success:
        await callback.answer("🗑 Задача удалена")
    else:
        await callback.answer("❌ Ошибка при удалении задачи")
        return
    
    # Обновляем сообщение со списком задач
    await refresh_tasks_message(callback, current_status, user_id)

@router.callback_query(lambda c: c.data.startswith("group_extend_"))
async def group_extend_task(callback: types.CallbackQuery, state: FSMContext):
    """Продлить просроченную задачу в групповом режиме"""
    parts = callback.data.split("_")
    task_id = int(parts[2])
    current_status = parts[3]
    
    await state.update_data(extending_task_id=task_id, current_status=current_status)
    
    keyboard = get_deadline_selection_keyboard()
    
    await callback.answer()

    await callback.message.answer(
        "⏰ Выберите новый дедлайн для задачи:",
        reply_markup=keyboard
    )
    await state.set_state(TaskStates.waiting_for_extend_deadline)

async def refresh_tasks_message(callback: types.CallbackQuery, status: str, user_id: int):
    """Обновляет сообщение со списком задач после изменения"""
    
    # Определяем заголовок в зависимости от статуса
    titles = {
        'active': "🔥 Активные задачи",
        'completed': "✅ Выполненные задачи",
        'failed': "❌ Невыполненные задачи",
        'overdue': "⚠️ Просроченные задачи"
    }
    
    title = titles.get(status, "Задачи")
    
    async with db.pool.acquire() as conn:
        # Получаем часовой пояс пользователя
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
        user_timezone = user['timezone'] if user else None
    
    # Получаем обновленные задачи (уже расшифрованные)
    tasks = await db.get_user_tasks(user_id, status)
    
    # Если задач больше нет, показываем пустое сообщение
    if not tasks:
        try:
            await callback.message.edit_text(f"{title}\n\nЗадач не найдено")
        except:
            pass
        return
    
    # Форматируем сообщение с учетом часового пояса
    message_text = format_tasks_message(tasks, title, user_timezone)
    
    # Создаем клавиатуру
    keyboard = create_tasks_keyboard(tasks, status)
    
    # Обновляем сообщение
    try:
        await callback.message.edit_text(message_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error updating message: {e}")

# ======= ОБРАБОТКА РАСШИРЕНИЯ ДЕДЛАЙНА =======

@router.message(TaskStates.waiting_for_extend_deadline)
async def process_extend_custom_deadline_input(message: types.Message, state: FSMContext):
    """Обработка ввода произвольного дедлайна при продлении"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    try:
        # Парсим дату в формате ДД.ММ.ГГГГ
        date_parts = message.text.strip().split('.')
        if len(date_parts) != 3:
            raise ValueError("Неверный формат")
        
        day, month, year = map(int, date_parts)
        
        # Получаем часовой пояс пользователя
        user_id = message.from_user.id
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
        
        current_time = get_user_time(user['timezone'])
        new_deadline = create_deadline_from_user_time(
            user['timezone'], year, month, day
        )
        
        if new_deadline is None:
            raise ValueError("Ошибка создания дедлайна")
        
        # Проверяем, что дата не в прошлом
        current_utc = normalize_datetime_for_db(current_time)
        if new_deadline < current_utc:
            await message.answer(
                "❌ Нельзя устанавливать дедлайн на прошедшую дату.\n"
                "Попробуйте еще раз:"
            )
            return
        
        # Создаем фиктивный callback для завершения процесса
        fake_callback = types.CallbackQuery(
            id="fake", from_user=message.from_user, chat_instance="fake", 
            message=message, data="fake"
        )
        
        await complete_extend_task(fake_callback, state, new_deadline)
        
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат даты!\n"
            "Используйте формат ДД.ММ.ГГГГ\n"
            "Например: 15.12.2024\n\n"
            "Попробуйте еще раз:"
        )

async def complete_extend_task(callback, state: FSMContext, new_deadline):
    """Завершает процесс продления задачи"""
    data = await state.get_data()
    task_id = data.get("extending_task_id")
    current_status = data.get("current_status", "overdue")
    user_id = callback.from_user.id
    
    normalized_deadline = normalize_datetime_for_db(new_deadline)
    
    async with db.pool.acquire() as conn:
        await conn.execute(
            """UPDATE tasks SET status = 'active', deadline = $1, marked_overdue_at = NULL
               WHERE task_id = $2 AND user_id = $3""",
            normalized_deadline, task_id, user_id
        )
        
        # Получаем часовой пояс для отображения
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    
    if new_deadline:
        display_deadline = pytz.UTC.localize(normalized_deadline).astimezone(pytz.timezone(user['timezone']))
        deadline_text = display_deadline.strftime('%d.%m.%Y')
    else:
        deadline_text = "без дедлайна"

    if hasattr(callback, 'id') and callback.id != "fake":
        await callback.answer(f"✅ Задача продлена до {deadline_text}")
    
    await callback.message.answer(
        f"✅ Задача продлена до {deadline_text}",
        reply_markup=get_tasks_menu_keyboard()
    )
    
    await state.clear()

# ======= СИСТЕМНЫЕ ФУНКЦИИ =======

async def check_overdue_tasks():
    """Проверка и пометка просроченных задач (вызывается планировщиком в 00:30)"""
    try:
        async with db.pool.acquire() as conn:
            # Получаем всех пользователей с их часовыми поясами
            users = await conn.fetch("SELECT user_id, timezone FROM users")
            
            for user in users:
                user_tz = pytz.timezone(user['timezone'])
                current_time = datetime.now(user_tz)
                current_utc = normalize_datetime_for_db(current_time)
                
                # Проверяем активные задачи с дедлайном
                overdue_tasks = await conn.fetch(
                    """SELECT task_id FROM tasks 
                       WHERE user_id = $1 AND status = 'active' 
                       AND deadline IS NOT NULL AND deadline < $2""",
                    user['user_id'], current_utc
                )
                
                if overdue_tasks:
                    # Проверяем лимит просроченных задач
                    overdue_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND status = 'overdue'",
                        user['user_id']
                    )
                    
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
        logger.error(f"Error checking overdue tasks: {e}")

async def enforce_task_limits(user_id: int, status: str):
    """Принудительное соблюдение лимитов задач"""
    try:
        async with db.pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND status = $2",
                user_id, status
            )
            
            if count > TASK_LIMITS[status]:
                excess = count - TASK_LIMITS[status]
                
                if status == 'completed':
                    # Удаляем самые старые выполненные
                    await conn.execute(
                        """DELETE FROM tasks WHERE task_id IN (
                            SELECT task_id FROM tasks 
                            WHERE user_id = $1 AND status = 'completed'
                            ORDER BY completed_at ASC LIMIT $2
                        )""",
                        user_id, excess
                    )
                elif status in ['failed', 'overdue']:
                    # Удаляем самые старые невыполненные/просроченные
                    order_field = 'completed_at' if status == 'failed' else 'marked_overdue_at'
                    await conn.execute(
                        f"""DELETE FROM tasks WHERE task_id IN (
                            SELECT task_id FROM tasks 
                            WHERE user_id = $1 AND status = $2
                            ORDER BY {order_field} ASC LIMIT $3
                        )""",
                        user_id, status, excess
                    )
                
                logger.info(f"Enforced limit for user {user_id}, status {status}, deleted {excess} tasks")
    
    except Exception as e:
        logger.error(f"Error enforcing task limits: {e}")
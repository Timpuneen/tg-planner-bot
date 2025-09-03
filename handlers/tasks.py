from aiogram import Router, types
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

def calculate_deadline(action, user_timezone, current_time):
    """Вычисляет дедлайн на основе действия"""
    if action == "today":
        return create_deadline_from_user_time(
            user_timezone, 
            current_time.year, current_time.month, current_time.day
        )
    elif action == "tomorrow":
        tomorrow = current_time + timedelta(days=1)
        return create_deadline_from_user_time(
            user_timezone,
            tomorrow.year, tomorrow.month, tomorrow.day
        )
    elif action == "week":
        days_until_sunday = 6 - current_time.weekday()
        target_date = current_time + timedelta(days=days_until_sunday)
        return create_deadline_from_user_time(
            user_timezone,
            target_date.year, target_date.month, target_date.day
        )
    elif action == "month":
        next_month = current_time.replace(day=28) + timedelta(days=4)
        last_day_of_month = next_month - timedelta(days=next_month.day)
        return create_deadline_from_user_time(
            user_timezone,
            last_day_of_month.year, last_day_of_month.month, last_day_of_month.day
        )
    elif action == "year":
        return create_deadline_from_user_time(
            user_timezone,
            current_time.year, 12, 31
        )
    elif action == "none":
        return None
    return None

def get_state_error_response(message_or_callback, is_callback=False):
    """Универсальная обработка ошибки отсутствия состояния"""
    error_msg = "❌ Ошибка: текст задачи потерян. Начните сначала."
    restart_msg = "Произошла ошибка. Пожалуйста, создайте задачу заново."
    keyboard = get_tasks_menu_keyboard()
    
    if is_callback:
        return error_msg, restart_msg, keyboard
    else:
        return "❌ Ошибка: текст задачи потерян. Начните создание задачи заново.", keyboard

async def validate_task_state(state: FSMContext, message_or_callback, is_callback=False):
    """Валидация состояния задачи"""
    data = await state.get_data()
    if not data.get("task_text"):
        logger.error("Task text is missing from state!")
        if is_callback:
            error_msg, restart_msg, keyboard = get_state_error_response(message_or_callback, True)
            await message_or_callback.answer(error_msg)
            await message_or_callback.message.answer(restart_msg, reply_markup=keyboard)
        else:
            error_msg, keyboard = get_state_error_response(message_or_callback, False)
            await message_or_callback.answer(error_msg, reply_markup=keyboard)
        await state.clear()
        return False
    return True

async def send_message_with_fallback(message_or_callback, text, reply_markup=None, is_callback=False):
    """Отправка сообщения с fallback для callback"""
    if is_callback and hasattr(message_or_callback, 'message') and hasattr(message_or_callback.message, 'edit_text'):
        try:
            await message_or_callback.message.edit_text(text, reply_markup=reply_markup)
        except:
            await message_or_callback.message.answer(text, reply_markup=reply_markup)
    else:
        target = message_or_callback.message if is_callback else message_or_callback
        await target.answer(text, reply_markup=reply_markup)

async def cleanup_unused_categories(user_id: int):
    """Удаляет неиспользуемые категории задач для пользователя"""
    try:
        async with db.pool.acquire() as conn:
            # Находим категории, которые не используются ни в одной задаче
            unused_categories = await conn.fetch(
                """SELECT tc.category_id, tc.name 
                   FROM task_categories tc
                   LEFT JOIN tasks t ON tc.name = t.category AND tc.user_id = t.user_id
                   WHERE tc.user_id = $1 AND t.task_id IS NULL""",
                user_id
            )
            
            if unused_categories:
                # Удаляем неиспользуемые категории
                category_ids = [cat['category_id'] for cat in unused_categories]
                await conn.execute(
                    "DELETE FROM task_categories WHERE category_id = ANY($1::int[])",
                    category_ids
                )
                
                category_names = [cat['name'] for cat in unused_categories]
                logger.info(f"Cleaned up {len(category_ids)} unused categories for user {user_id}: {category_names}")
                
    except Exception as e:
        logger.error(f"Error cleaning up unused categories for user {user_id}: {e}")

async def cleanup_unused_categories_all_users():
    """Очистка неиспользуемых категорий для всех пользователей (для планировщика)"""
    try:
        async with db.pool.acquire() as conn:
            # Получаем всех пользователей, у которых есть категории
            users = await conn.fetch(
                "SELECT DISTINCT user_id FROM task_categories"
            )
            
        for user in users:
            await cleanup_unused_categories(user['user_id'])
            
    except Exception as e:
        logger.error(f"Error in global category cleanup: {e}")

# ======= СОЗДАНИЕ ЗАДАЧ =======

@router.message(lambda message: message.text == "➕ Создать новую задачу")
async def create_task_start(message: types.Message, state: FSMContext):
    """Начало создания новой задачи"""
    # Очищаем состояние перед началом нового процесса
    await state.clear()
    
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
    
    # Получаем последние 10 уникальных категорий пользователя, которые действительно используются
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
    
    # ВАЖНО: Отвечаем на callback
    await callback.answer()
    
    # Проверяем состояние перед обработкой категории
    if not await validate_task_state(state, callback, True):
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
            # Уже вызвали callback.answer(), поэтому просто возвращаемся
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
    if not await validate_task_state(state, message, False):
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
    text = f"✅ {category_text}\n\nВыберите дедлайн:"
    
    await send_message_with_fallback(callback, text, keyboard, hasattr(callback, 'id'))
    await state.set_state(TaskStates.waiting_for_deadline)

async def process_deadline_common(callback: types.CallbackQuery, state: FSMContext, is_extend=False):
    """Общая логика обработки дедлайна"""
    action = callback.data.replace("deadline_", "")
    user_id = callback.from_user.id
    
    # ВАЖНО: Сразу отвечаем на callback, чтобы убрать подсветку кнопки
    await callback.answer()
    
    # Получаем часовой пояс пользователя
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    
    current_time = get_user_time(user['timezone'])
    
    try:
        if action == "custom":
            prompt = "📅 Введите новый дедлайн в формате ДД.ММ.ГГГГ\nНапример: 15.12.2024" if is_extend else "📅 Введите дедлайн в формате ДД.ММ.ГГГГ\nНапример: 15.12.2024"
            await callback.message.edit_text(prompt)
            next_state = TaskStates.waiting_for_extend_deadline if is_extend else TaskStates.waiting_for_custom_deadline
            await state.set_state(next_state)
            return
        
        deadline = calculate_deadline(action, user['timezone'], current_time)
        
        if is_extend:
            await complete_extend_task(callback, state, deadline)
        else:
            await save_task(callback, state, deadline, is_from_message=False)
            
    except Exception as e:
        logger.error(f"Error creating deadline: {e}")
        # Уже вызвали callback.answer() выше, поэтому не нужно повторно

@router.callback_query(lambda c: c.data.startswith("deadline_") and c.message.text.startswith("⏰ Выберите новый дедлайн"))
async def process_extend_deadline_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора дедлайна при продлении задачи"""
    await process_deadline_common(callback, state, is_extend=True)

@router.callback_query(lambda c: c.data.startswith("deadline_"))
async def process_deadline_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора дедлайна"""
    # Проверяем, не обрабатывается ли это расширение задачи
    if callback.message.text.startswith("⏰ Выберите новый дедлайн"):
        return  # Обработка в process_extend_deadline_callback
    
    # Проверяем состояние перед обработкой дедлайна
    if not await validate_task_state(state, callback, True):
        return
    
    await process_deadline_common(callback, state, is_extend=False)

async def process_custom_deadline_common(message: types.Message, state: FSMContext, is_extend=False):
    """Общая логика для обработки произвольного дедлайна"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    # Проверяем состояние только для создания задач, не для продления
    if not is_extend and not await validate_task_state(state, message, False):
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
        
        if is_extend:
            # Для продления создаем фиктивный callback
            fake_callback = types.CallbackQuery(
                id="fake", from_user=message.from_user, chat_instance="fake", 
                message=message, data="fake"
            )
            await complete_extend_task(fake_callback, state, deadline)
        else:
            # Для создания задачи передаем message напрямую с флагом
            await save_task(message, state, deadline, is_from_message=True)
        
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing custom deadline: {e}")
        await message.answer(
            "❌ Неверный формат даты!\n"
            "Используйте формат ДД.ММ.ГГГГ\n"
            "Например: 15.12.2024\n\n"
            "Попробуйте еще раз:"
        )

@router.message(TaskStates.waiting_for_custom_deadline)
async def process_custom_deadline(message: types.Message, state: FSMContext):
    """Обработка ввода произвольного дедлайна"""
    await process_custom_deadline_common(message, state, is_extend=False)

@router.message(TaskStates.waiting_for_extend_deadline)
async def process_extend_custom_deadline_input(message: types.Message, state: FSMContext):
    """Обработка ввода произвольного дедлайна при продлении"""
    await process_custom_deadline_common(message, state, is_extend=True)

async def save_task(callback_or_message, state, deadline, is_from_message=False):
    """Сохранение задачи в базу данных"""
    try:
        data = await state.get_data()
        task_text = data.get("task_text")
        category = data.get("category")
        
        # Получаем user_id в зависимости от типа объекта
        if is_from_message:
            user_id = callback_or_message.from_user.id
            message_obj = callback_or_message
        else:
            user_id = callback_or_message.from_user.id
            message_obj = callback_or_message.message
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: убеждаемся, что task_text не None
        if not task_text:
            logger.error(f"Critical error: task_text is None or empty! Data keys: {list(data.keys())}")
            await message_obj.answer(
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
        
        # Сохраняем категорию если она новая (только добавляем в task_categories, если она не существует)
        if category:
            async with db.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO task_categories (user_id, name) 
                       VALUES ($1, $2) ON CONFLICT (user_id, name) DO NOTHING""",
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
        
        # Отправляем сообщение напрямую через message объект
        await message_obj.answer(success_message)
        
        await message_obj.answer(
            "Что дальше?",
            reply_markup=get_tasks_menu_keyboard()
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error saving task: {e}", exc_info=True)
        # Используем правильный message объект для отправки ошибки
        message_obj = callback_or_message if is_from_message else callback_or_message.message
        await message_obj.answer(
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
    
    def format_task_line(task, counter):
        """Форматирует строку задачи"""
        deadline_text = ""
        task_prefix = ""
        
        if task.get('deadline'):
            # Конвертируем deadline в пользовательский часовой пояс
            if user_timezone:
                deadline_display = pytz.UTC.localize(task['deadline']).astimezone(pytz.timezone(user_timezone))
                deadline_date = deadline_display.date()
                
                # Выделяем задачи на сегодня ТОЛЬКО если они активные
                if (current_date and deadline_date == current_date and 
                    task.get('status') == 'active'):
                    task_prefix = "🚨 "
                
                deadline_text = f" (📅 {deadline_display.strftime('%d.%m.%Y')})"
            else:
                deadline_text = f" (📅 {task['deadline'].strftime('%d.%m.%Y')})"
        elif task.get('completed_at'):
            deadline_text = f" (📅 {task['completed_at'].strftime('%d.%m.%Y')})"
        
        return f"{counter}. {task_prefix}{task['text']}{deadline_text}"
    
    # Задачи по категориям
    for category_name, category_tasks in categorized_tasks.items():
        message_parts.append(f"📂 {category_name}")
        
        for task in category_tasks:
            task_text = format_task_line(task, task_counter)
            message_parts.append(task_text)
            task_counter += 1
        
        message_parts.append("")
    
    # Задачи без категории
    if uncategorized_tasks:
        message_parts.append("📂 Без категории")
        
        for task in uncategorized_tasks:
            task_text = format_task_line(task, task_counter)
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
async def view_tasks_menu(message: types.Message, state: FSMContext):
    """Меню просмотра задач с автообновлением статусов"""
    # Очищаем состояние перед просмотром
    await state.clear()
    
    # Сначала обновляем просроченные задачи
    await update_overdue_tasks_for_user(message.from_user.id)
    
    # Очищаем неиспользуемые категории
    await cleanup_unused_categories(message.from_user.id)
    
    await message.answer(
        "👀 Просмотр задач\n\n"
        "Выберите категорию:",
        reply_markup=get_task_view_menu_keyboard()
    )

# Обработчики просмотра задач
TASK_VIEW_HANDLERS = {
    "🔥 Активные": ("active", "🔥 Активные задачи"),
    "✅ Выполненные": ("completed", "✅ Выполненные задачи"),
    "❌ Невыполненные": ("failed", "❌ Невыполненные задачи"),
    "⚠️ Просроченные": ("overdue", "⚠️ Просроченные задачи")
}

async def handle_task_view(message: types.Message, status: str, title: str, update_overdue: bool = False):
    """Универсальный обработчик просмотра задач"""
    if update_overdue:
        await update_overdue_tasks_for_user(message.from_user.id)
    await send_tasks_group_message(message, status, title)

@router.message(lambda message: message.text in TASK_VIEW_HANDLERS)
async def view_tasks_handler(message: types.Message):
    """Универсальный обработчик для всех типов просмотра задач"""
    status, title = TASK_VIEW_HANDLERS[message.text]
    update_overdue = status in ['active', 'overdue']
    await handle_task_view(message, status, title, update_overdue)

# ======= ОБРАБОТЧИКИ ГРУППОВЫХ ДЕЙСТВИЙ =======

async def handle_group_action(callback: types.CallbackQuery, action: str):
    """Универсальный обработчик групповых действий с задачами"""
    parts = callback.data.split("_")
    task_id = int(parts[2])
    current_status = parts[3]
    user_id = callback.from_user.id
    
    success = False
    message = ""
    
    if action == "complete":
        success = await db.update_task_status(task_id, user_id, 'completed')
        if success:
            await enforce_task_limits(user_id, 'completed')
            message = "✅ Задача отмечена как выполненная!"
    elif action == "fail":
        success = await db.update_task_status(task_id, user_id, 'failed')
        if success:
            await enforce_task_limits(user_id, 'failed')
            message = "❌ Задача отмечена как невыполненная"
    elif action == "delete":
        success = await db.delete_task(task_id, user_id)
        # После удаления задачи очищаем неиспользуемые категории
        if success:
            await cleanup_unused_categories(user_id)
        message = "🗑 Задача удалена" if success else "❌ Ошибка при удалении задачи"
    
    if not success and action != "delete":
        await callback.answer("❌ Ошибка при обновлении задачи")
        return
    
    await callback.answer(message)
    
    # Обновляем сообщение со списком задач
    await refresh_tasks_message(callback, current_status, user_id)

@router.callback_query(lambda c: c.data.startswith(("group_complete_", "group_fail_", "group_delete_")))
async def group_action_handler(callback: types.CallbackQuery):
    """Универсальный обработчик для complete, fail, delete"""
    action = callback.data.split("_")[1]  # complete, fail, или delete
    await handle_group_action(callback, action)

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
    
    # Используем правильный message объект
    message_obj = callback.message
    await message_obj.answer(
        f"✅ Задача продлена до {deadline_text}",
        reply_markup=get_tasks_menu_keyboard()
    )
    
    await state.clear()

# ======= СИСТЕМНЫЕ ФУНКЦИИ =======

async def process_overdue_tasks_for_users(users):
    """Обработка просроченных задач для списка пользователей"""
    for user in users:
        user_tz = pytz.timezone(user['timezone'])
        current_time = datetime.now(user_tz)
        current_utc = normalize_datetime_for_db(current_time)
        
        async with db.pool.acquire() as conn:
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

async def check_overdue_tasks():
    """Проверка и пометка просроченных задач (вызывается планировщиком в 00:00)"""
    try:
        async with db.pool.acquire() as conn:
            # Получаем всех пользователей с их часовыми поясами
            users = await conn.fetch("SELECT user_id, timezone FROM users")
            
        await process_overdue_tasks_for_users(users)
        
        # После обработки просроченных задач, очищаем неиспользуемые категории для всех пользователей
        await cleanup_unused_categories_all_users()
        
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
                
                # Определяем поле для сортировки и SQL запрос
                if status == 'completed':
                    order_field = 'completed_at'
                elif status == 'failed':
                    order_field = 'completed_at'
                elif status == 'overdue':
                    order_field = 'marked_overdue_at'
                else:
                    return  # Неизвестный статус
                
                await conn.execute(
                    f"""DELETE FROM tasks WHERE task_id IN (
                        SELECT task_id FROM tasks 
                        WHERE user_id = $1 AND status = $2
                        ORDER BY {order_field} ASC LIMIT $3
                    )""",
                    user_id, status, excess
                )
                
                logger.info(f"Enforced limit for user {user_id}, status {status}, deleted {excess} tasks")
                
                # После принудительной очистки задач, очищаем неиспользуемые категории
                await cleanup_unused_categories(user_id)
    
    except Exception as e:
        logger.error(f"Error enforcing task limits: {e}")
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import logging

from database.connection import db
from keyboards.keyboards import get_timezone_keyboard, get_main_menu_keyboard
from services.timezone_service import get_timezone_from_location

logger = logging.getLogger(__name__)

router = Router()

class TimezoneSetup(StatesGroup):
    waiting_for_timezone = State()

@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    # Проверяем, есть ли пользователь в базе данных
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    
    if user:
        # Пользователь уже существует, показываем главное меню
        await message.answer(
            "Добро пожаловать обратно! 👋\n"
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        # Новый пользователь, нужно выбрать часовой пояс
        await message.answer(
            "Добро пожаловать! 👋\n\n"
            "Для корректной работы бота нужно выбрать ваш часовой пояс.\n"
            "Выберите из списка популярных часовых поясов или отправьте геолокацию:",
            reply_markup=get_timezone_keyboard()
        )
        await state.set_state(TimezoneSetup.waiting_for_timezone)

@router.callback_query(lambda c: c.data.startswith("tz_"))
async def timezone_selected(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора часового пояса из списка"""
    timezone = callback.data.replace("tz_", "")
    user_id = callback.from_user.id
    
    # Получаем текущее время в выбранном часовом поясе
    from datetime import datetime
    import pytz
    
    tz = pytz.timezone(timezone)
    current_time = datetime.now(tz).strftime("%H:%M")
    current_date = datetime.now(tz).strftime("%d.%m.%Y")
    
    # Сохраняем пользователя в базу данных
    async with db.pool.acquire() as conn:
        # Проверяем, существует ли уже пользователь перед добавлением напоминаний
        existing_user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        is_new_user = existing_user is None
        
        # Используем ON CONFLICT, но добавляем напоминания только для новых пользователей
        await conn.execute(
            "INSERT INTO users (user_id, timezone) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET timezone = $2",
            user_id, timezone
        )
        
        # Добавляем встроенные напоминания только для нового пользователя
        if is_new_user:
            await add_built_in_reminders(user_id, conn)
    
    await callback.message.edit_text(
        f"✅ Часовой пояс установлен: {timezone}\n"
        f"🕐 Текущее время: {current_time}\n"
        f"📅 Дата: {current_date}\n\n"
        "Теперь вы можете использовать все функции бота!"
    )
    
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_menu_keyboard()
    )
    
    await state.clear()

@router.callback_query(lambda c: c.data == "send_location")
async def request_location(callback: types.CallbackQuery):
    """Запрос геолокации"""
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await callback.message.answer(
        "Пожалуйста, отправьте вашу геолокацию:",
        reply_markup=keyboard
    )

@router.message(lambda message: message.location is not None)
async def location_received(message: types.Message, state: FSMContext):
    """Обработчик получения геолокации"""
    location = message.location
    user_id = message.from_user.id
    
    try:
        # Определяем часовой пояс по координатам
        timezone = await get_timezone_from_location(location.latitude, location.longitude)
        
        if timezone:
            # Получаем текущее время в этом часовом поясе
            from datetime import datetime
            import pytz
            
            tz = pytz.timezone(timezone)
            current_time = datetime.now(tz).strftime("%H:%M")
            current_date = datetime.now(tz).strftime("%d.%m.%Y")
            
            # Сохраняем пользователя в базу данных
            async with db.pool.acquire() as conn:
                # Проверяем, существует ли уже пользователь перед добавлением напоминаний
                existing_user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
                is_new_user = existing_user is None
                
                # Используем ON CONFLICT, но добавляем напоминания только для новых пользователей
                await conn.execute(
                    "INSERT INTO users (user_id, timezone) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET timezone = $2",
                    user_id, timezone
                )
                
                # Добавляем встроенные напоминания только для нового пользователя
                if is_new_user:
                    await add_built_in_reminders(user_id, conn)
            
            await message.answer(
                f"✅ Часовой пояс определен: {timezone}\n"
                f"🕐 Текущее время: {current_time}\n"
                f"📅 Дата: {current_date}\n\n"
                "Теперь вы можете использовать все функции бота!",
                reply_markup=get_main_menu_keyboard()
            )
            
            await state.clear()
        else:
            await message.answer(
                "❌ Не удалось определить часовой пояс по вашей геолокации.\n"
                "Пожалуйста, выберите из списка:",
                reply_markup=get_timezone_keyboard()
            )
    
    except Exception as e:
        await message.answer(
            "❌ Произошла ошибка при определении часового пояса.\n"
            "Пожалуйста, выберите из списка:",
            reply_markup=get_timezone_keyboard()
        )

async def add_built_in_reminders(user_id: int, conn):
    """Добавляет встроенные напоминания для нового пользователя"""
    # Теперь добавляем только одно напоминание, которое будет определять тип в зависимости от дня
    built_in_reminder = (
        "Умное ежедневное напоминание о подведении итогов", 
        "0 22 * * *", 
        "ежедневно в 22:00 (с учетом приоритета: год/месяц/неделя/день)"
    )
     
    logger.info(f"Adding built-in reminder for new user {user_id}")
    
    try:
        await conn.execute(
            """INSERT INTO reminders (user_id, text, reminder_type, cron_expression, is_active, is_built_in) 
               VALUES ($1, $2, 'recurring', $3, TRUE, TRUE)""",
            user_id, built_in_reminder[0], built_in_reminder[1]
        )
        logger.debug(f"Added smart built-in reminder for user {user_id}")
    except Exception as e:
        logger.error(f"Error adding built-in reminder for user {user_id}: {e}")
    
    logger.info(f"Successfully added built-in reminder for user {user_id}")

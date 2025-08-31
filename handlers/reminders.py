from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import pytz
import re

from database.connection import db
from keyboards.keyboards import get_reminders_menu_keyboard, get_back_to_main_keyboard
from services.openai_service import parse_reminder_time
from services.timezone_service import get_user_time

router = Router()

class ReminderStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time = State()
    confirming_reminder = State()

def parse_cron_description(cron_expression):
    """Преобразует cron выражение в читаемое описание"""
    if not cron_expression:
        return "неизвестно"
    
    # Функция для преобразования интервалов в читаемый формат
    def format_interval(minutes):
        if minutes < 60:
            return f"каждые {minutes} минут"
        elif minutes == 60:
            return "каждый час"
        elif minutes < 1440:  # меньше суток
            hours = minutes // 60
            remaining_minutes = minutes % 60
            if remaining_minutes == 0:
                if hours == 1:
                    return "каждый час"
                else:
                    return f"каждые {hours} часа" if 2 <= hours <= 4 else f"каждые {hours} часов"
            else:
                return f"каждые {hours}ч {remaining_minutes}мин"
        else:  # больше суток
            days = minutes // 1440
            remaining_hours = (minutes % 1440) // 60
            remaining_minutes = minutes % 60
            
            result = f"каждые {days} " + ("день" if days == 1 else ("дня" if 2 <= days <= 4 else "дней"))
            if remaining_hours > 0:
                result += f" {remaining_hours}ч"
            if remaining_minutes > 0:
                result += f" {remaining_minutes}мин"
            return result

    def get_yearly_description(hour, day, month, minute='0'):
        """Возвращает описание для ежегодных cron выражений"""
        months = {
            '1': 'января', '2': 'февраля', '3': 'марта', '4': 'апреля',
            '5': 'мая', '6': 'июня', '7': 'июля', '8': 'августа',
            '9': 'сентября', '10': 'октября', '11': 'ноября', '12': 'декабря'
        }
        
        month_name = months.get(month, f"{month} месяца")
        time_str = f"{hour:0>2}:{minute:0>2}" if minute != '0' else f"{hour:0>2}:00"
        
        # Особые случаи
        if day == '31' and month == '12':
            return f"каждый Новый год (31 декабря) в {time_str}"
        elif day == '1' and month == '1':
            return f"каждый Новый год (1 января) в {time_str}"
        else:
            return f"каждый год {day} {month_name} в {time_str}"
    
    # Расширенные паттерны для различных cron выражений
    cron_patterns = {
        # Интервальные выражения
        r'\*/(\d+) \* \* \* \*': lambda m: format_interval(int(m.group(1))),
        r'0 \*/(\d+) \* \* \*': lambda m: format_interval(int(m.group(1)) * 60),
        r'0 0 \*/(\d+) \* \*': lambda m: format_interval(int(m.group(1)) * 1440),
        
        # Ежедневные
        r'0 (\d+) \* \* \*': lambda m: f"каждый день в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* \*': lambda m: f"каждый день в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Еженедельные (0 = воскресенье, 1 = понедельник, ..., 6 = суббота)
        r'0 (\d+) \* \* 1': lambda m: f"каждый понедельник в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 1': lambda m: f"каждый понедельник в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 2': lambda m: f"каждый вторник в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 2': lambda m: f"каждый вторник в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 3': lambda m: f"каждую среду в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 3': lambda m: f"каждую среду в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 4': lambda m: f"каждый четверг в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 4': lambda m: f"каждый четверг в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 5': lambda m: f"каждую пятницу в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 5': lambda m: f"каждую пятницу в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 6': lambda m: f"каждую субботу в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 6': lambda m: f"каждую субботу в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 0': lambda m: f"каждое воскресенье в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 0': lambda m: f"каждое воскресенье в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Ежемесячные с последним днем месяца (L)
        r'0 (\d+) L \* \*': lambda m: f"в последний день каждого месяца в {m.group(1):0>2}:00",
        r'(\d+) (\d+) L \* \*': lambda m: f"в последний день каждого месяца в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Ежемесячные с конкретным числом
        r'0 (\d+) (\d+) \* \*': lambda m: f"каждый месяц {m.group(2)} числа в {m.group(1):0>2}:00",
        r'(\d+) (\d+) (\d+) \* \*': lambda m: f"каждый месяц {m.group(3)} числа в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Ежегодные (конкретная дата)
        r'0 0 (\d+) (\d+) \*': lambda m: get_yearly_description('0', m.group(1), m.group(2)),
        r'0 (\d+) (\d+) (\d+) \*': lambda m: get_yearly_description(m.group(1), m.group(2), m.group(3)),
        r'(\d+) (\d+) (\d+) (\d+) \*': lambda m: get_yearly_description(m.group(2), m.group(3), m.group(4), m.group(1)),
        
        # Рабочие дни (понедельник-пятница)
        r'0 (\d+) \* \* 1-5': lambda m: f"по будням в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 1-5': lambda m: f"по будням в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Выходные (суббота-воскресенье)
        r'0 (\d+) \* \* 0,6': lambda m: f"по выходным в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 0,6': lambda m: f"по выходным в {m.group(2):0>2}:{m.group(1):0>2}",
        r'0 (\d+) \* \* 6,0': lambda m: f"по выходным в {m.group(1):0>2}:00",
        r'(\d+) (\d+) \* \* 6,0': lambda m: f"по выходным в {m.group(2):0>2}:{m.group(1):0>2}",
        
        # Каждый час и минуты
        r'0 \* \* \* \*': lambda m: "каждый час",
        r'(\d+) \* \* \* \*': lambda m: f"каждый час в {m.group(1):0>2} минут",
        
        # Несколько раз в день
        r'0 (\d+),(\d+) \* \* \*': lambda m: f"каждый день в {m.group(1):0>2}:00 и {m.group(2):0>2}:00",
        
        # Специальные интервалы
        r'0 0 \* \* \*': lambda m: "каждый день в полночь",
        r'0 12 \* \* \*': lambda m: "каждый день в полдень",
        r'30 (\d+) \* \* \*': lambda m: f"каждый день в {m.group(1):0>2}:30",
        r'15 (\d+) \* \* \*': lambda m: f"каждый день в {m.group(1):0>2}:15",
        r'45 (\d+) \* \* \*': lambda m: f"каждый день в {m.group(1):0>2}:45",
    }
    
    import re
    
    for pattern, formatter in cron_patterns.items():
        match = re.match(pattern, cron_expression)
        if match:
            return formatter(match)
    
    # Если паттерн не найден, возвращаем более понятное описание
    return f"по расписанию: {cron_expression}"

def format_reminders_text_and_keyboard(reminders):
    """Форматирует текст и клавиатуру для списка напоминаний"""
    text = "📋 Ваши напоминания:\n\n"
    keyboard_buttons = []
    
    for i, reminder in enumerate(reminders, 1):
        status = "🟢" if reminder['is_active'] else "🔴"
        built_in = " (встроенное)" if reminder['is_built_in'] else ""
        
        # Показываем полный текст напоминания
        full_text = reminder['text']
        
        if reminder['reminder_type'] == 'once':
            time_info = reminder['trigger_time'].strftime("%d.%m.%Y %H:%M")
            text += f"{status} {i}. {full_text}\n"
            text += f"   📅 Разовое: {time_info}{built_in}\n\n"
        else:
            # Для повторяющихся напоминаний показываем частоту
            frequency_description = parse_cron_description(reminder['cron_expression'])
            text += f"{status} {i}. {full_text}\n"
            text += f"   🔄 {frequency_description}{built_in}\n\n"
        
        # Добавляем кнопки для управления напоминанием
        if reminder['reminder_type'] == 'recurring':
            # Для повторяющихся напоминаний: кнопки удалить и отключить/включить в одной строке
            if reminder['is_active']:
                keyboard_buttons.append([
                    types.InlineKeyboardButton(
                        text=f"🗑 Удалить #{i}", 
                        callback_data=f"delete_reminder_{reminder['reminder_id']}"
                    ),
                    types.InlineKeyboardButton(
                        text=f"🔴 Отключить #{i}", 
                        callback_data=f"disable_reminder_{reminder['reminder_id']}"
                    )
                ])
            else:
                keyboard_buttons.append([
                    types.InlineKeyboardButton(
                        text=f"🗑 Удалить #{i}", 
                        callback_data=f"delete_reminder_{reminder['reminder_id']}"
                    ),
                    types.InlineKeyboardButton(
                        text=f"🟢 Включить #{i}", 
                        callback_data=f"enable_reminder_{reminder['reminder_id']}"
                    )
                ])
        else:
            # Для разовых напоминаний: только кнопка удаления в отдельной строке
            keyboard_buttons.append([
                types.InlineKeyboardButton(
                    text=f"🗑 Удалить #{i}", 
                    callback_data=f"delete_reminder_{reminder['reminder_id']}"
                )
            ])
    
    return text, keyboard_buttons

async def update_reminders_list_message(message: types.Message, user_id: int):
    """Обновляет сообщение со списком напоминаний"""
    # Используем метод из database/connection.py, который автоматически расшифровывает данные
    reminders = await db.get_user_reminders(user_id)
    
    if not reminders:
        try:
            await message.edit_text(
                "📋 У вас пока нет напоминаний",
                reply_markup=None
            )
        except Exception as e:
            print(f"Error editing message: {e}")
        return
    
    text, keyboard_buttons = format_reminders_text_and_keyboard(reminders)
    
    # Telegram имеет лимит на длину сообщения (4096 символов)
    # Если сообщение слишком длинное, используем только первые 4000 символов
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (список слишком большой)"
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None
    
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        print(f"Error editing message: {e}")
        # Если редактирование не удалось, отправляем новое сообщение
        try:
            await message.answer(text, reply_markup=keyboard)
        except Exception as e2:
            print(f"Error sending new message: {e2}")

async def send_reminders_list(message: types.Message, user_id: int):
    """Отправляет список напоминаний с обработкой длинных сообщений"""
    # Используем метод из database/connection.py, который автоматически расшифровывает данные
    reminders = await db.get_user_reminders(user_id)
    
    if not reminders:
        await message.answer(
            "📋 У вас пока нет напоминаний",
            reply_markup=get_reminders_menu_keyboard()
        )
        return
    
    text, keyboard_buttons = format_reminders_text_and_keyboard(reminders)
    
    # Telegram имеет лимит на длину сообщения (4096 символов)
    # Если сообщение слишком длинное, разбиваем его на части
    if len(text) > 4000:
        # Отправляем по частям
        messages = []
        current_message = "📋 Ваши напоминания:\n\n"
        lines = text.split('\n\n')[1:]  # Убираем заголовок
        
        for line_group in lines:
            if len(current_message + line_group + '\n\n') > 4000:
                messages.append(current_message.rstrip())
                current_message = line_group + '\n\n'
            else:
                current_message += line_group + '\n\n'
        
        if current_message.strip():
            messages.append(current_message.rstrip())
        
        # Отправляем все части кроме последней без клавиатуры
        for msg in messages[:-1]:
            await message.answer(msg)
        
        # Последнюю часть отправляем с клавиатурой
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None
        await message.answer(messages[-1] if messages else "Список напоминаний пуст", reply_markup=keyboard)
    else:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None
        await message.answer(text, reply_markup=keyboard)

@router.message(lambda message: message.text == "➕ Разовое напоминание")
async def create_once_reminder(message: types.Message, state: FSMContext):
    """Создание разового напоминания"""
    # Очищаем предыдущее состояние перед началом нового процесса
    await state.clear()
    
    await message.answer(
        "📝 Создание разового напоминания\n\n"
        "Введите текст напоминания:",
        reply_markup=get_back_to_main_keyboard()
    )
    
    await state.set_state(ReminderStates.waiting_for_text)
    await state.update_data(reminder_type="once")

@router.message(lambda message: message.text == "🔄 Повторяющееся напоминание")
async def create_recurring_reminder(message: types.Message, state: FSMContext):
    """Создание повторяющегося напоминания"""
    # Очищаем предыдущее состояние перед началом нового процесса
    await state.clear()
    
    await message.answer(
        "🔄 Создание повторяющегося напоминания\n\n"
        "Введите текст напоминания:",
        reply_markup=get_back_to_main_keyboard()
    )
    
    await state.set_state(ReminderStates.waiting_for_text)
    await state.update_data(reminder_type="recurring")

@router.message(lambda message: message.text == "📋 Список напоминаний")
async def list_reminders(message: types.Message, state: FSMContext):
    """Просмотр списка напоминаний"""
    # Очищаем любое активное состояние при переходе к списку напоминаний
    await state.clear()
    
    user_id = message.from_user.id
    await send_reminders_list(message, user_id)
        
@router.message(ReminderStates.waiting_for_text)
async def process_reminder_text(message: types.Message, state: FSMContext):
    """Обработка текста напоминания"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    reminder_text = message.text
    data = await state.get_data()
    reminder_type = data.get("reminder_type")
    
    await state.update_data(reminder_text=reminder_text)
    
    type_text = "разового" if reminder_type == "once" else "повторяющегося"
    
    if reminder_type == "once":
        examples = (
            "Примеры:\n"
            "• 'завтра в 15:00'\n"
            "• 'через 2 часа'\n"
            "• 'сегодня в 20:30'\n"
            "• 'в пятницу в 14:00'"
        )
    else:
        examples = (
            "Примеры:\n"
            "• 'каждый день в 9 утра'\n"
            "• 'каждый понедельник в 10:00'\n"
            "• 'каждую пятницу в 18:00'\n"
            "• 'каждые 30 минут'"
        )
    
    await message.answer(
        f"✅ Текст {type_text} напоминания: {reminder_text}\n\n"
        f"Теперь укажите, когда вы хотите получить это напоминание.\n"
        f"{examples}",
        reply_markup=get_back_to_main_keyboard()
    )
    
    await state.set_state(ReminderStates.waiting_for_time)

@router.message(ReminderStates.waiting_for_time)
async def process_reminder_time(message: types.Message, state: FSMContext):
    """Обработка времени напоминания"""
    if message.text == "🏠 Главное меню":
        await state.clear()
        return
    
    user_id = message.from_user.id
    time_input = message.text
    
    # Получаем данные пользователя
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    
    if not user:
        await message.answer("❌ Ошибка: пользователь не найден")
        await state.clear()
        return
    
    data = await state.get_data()
    reminder_type = data.get("reminder_type")
    reminder_text = data.get("reminder_text")
    
    # Получаем текущее время в часовом поясе пользователя
    current_time = get_user_time(user['timezone'])
    current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
    
    await message.answer("⏳ Обрабатываю время напоминания...")
    
    # Парсим время через OpenAI
    parsed_result = await parse_reminder_time(
        time_input, 
        current_time_str, 
        user['timezone'], 
        reminder_type
    )
    
    if not parsed_result.get("success"):
        await message.answer(
            f"❌ Не удалось понять время напоминания: {parsed_result.get('error', 'Неизвестная ошибка')}\n\n"
            "Попробуйте еще раз. Укажите время более точно:",
            reply_markup=get_back_to_main_keyboard()
        )
        return
    
    # Дополнительная проверка: для разовых напоминаний время не должно быть в прошлом
    if (parsed_result.get("type") == "once" and 
        parsed_result.get("datetime") and 
        reminder_type == "once"):
        
        reminder_datetime = datetime.strptime(parsed_result["datetime"], "%Y-%m-%d %H:%M:%S")
        current_datetime = datetime.strptime(current_time_str, "%Y-%m-%d %H:%M:%S")
        
        if reminder_datetime < current_datetime:
            await message.answer(
                "❌ Время напоминания не может быть в прошлом. "
                "Пожалуйста, укажите будущее время для разового напоминания.",
                reply_markup=get_back_to_main_keyboard()
            )
            return
    
    # Сохраняем распарсенные данные
    await state.update_data(parsed_result=parsed_result)
    
    # Формируем сообщение с подтверждением
    if reminder_type == "once":
        confirmation_text = (
            f"✅ Разовое напоминание:\n"
            f"📝 Текст: {reminder_text}\n"
            f"⏰ Время: {parsed_result.get('datetime')}\n\n"
            f"Все верно?"
        )
    else:
        confirmation_text = (
            f"✅ Повторяющееся напоминание:\n"
            f"📝 Текст: {reminder_text}\n"
            f"🔄 Расписание: {parsed_result.get('description')}\n\n"
            f"Все верно?"
        )
    
    # Клавиатура подтверждения
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Да, создать", callback_data="confirm_reminder"),
            types.InlineKeyboardButton(text="❌ Нет, изменить", callback_data="reject_reminder")
        ]
    ])
    
    await message.answer(confirmation_text, reply_markup=keyboard)
    await state.set_state(ReminderStates.confirming_reminder)

@router.callback_query(lambda c: c.data == "confirm_reminder")
async def confirm_reminder(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение создания напоминания с учетом часовых поясов"""
    user_id = callback.from_user.id
    data = await state.get_data()
    
    reminder_text = data.get("reminder_text")
    reminder_type = data.get("reminder_type")
    parsed_result = data.get("parsed_result")
    
    try:
        # Получаем часовой пояс пользователя
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
        
        if not user:
            await callback.message.edit_text("❌ Ошибка: пользователь не найден")
            await state.clear()
            return
        
        # Импортируем планировщик и функции для работы с часовыми поясами
        from services.scheduler_service import get_scheduler
        from services.timezone_service import convert_user_time_to_scheduler_timezone, get_scheduler_timezone
        
        scheduler = get_scheduler()
        
        # Сохраняем напоминание в базу данных используя методы с шифрованием
        if reminder_type == "once":
            # Разовое напоминание
            user_trigger_time = datetime.strptime(parsed_result["datetime"], "%Y-%m-%d %H:%M:%S")
            
            # 🎯 КЛЮЧЕВОЙ МОМЕНТ: Преобразуем время пользователя в время планировщика
            scheduler_trigger_time = convert_user_time_to_scheduler_timezone(
                user_trigger_time,
                user['timezone'],
                get_scheduler_timezone()
            )
            
            # Логируем для отладки
            print(f"User time ({user['timezone']}): {user_trigger_time}")
            print(f"Scheduler time ({get_scheduler_timezone()}): {scheduler_trigger_time}")
            
            # Используем метод create_reminder из database/connection.py (с шифрованием)
            reminder_id = await db.create_reminder(
                user_id=user_id,
                text=reminder_text,
                reminder_type="once",
                trigger_time=user_trigger_time,
                cron_expression=None,
                is_built_in=False
            )
            
            # Добавляем в планировщик ВРЕМЯ ПЛАНИРОВЩИКА
            if scheduler:
                await scheduler.add_once_reminder(
                    reminder_id, user_id, reminder_text, scheduler_trigger_time
                )
            
        else:
            # Повторяющееся напоминание
            # Используем метод create_reminder из database/connection.py (с шифрованием)
            reminder_id = await db.create_reminder(
                user_id=user_id,
                text=reminder_text,
                reminder_type="recurring",
                trigger_time=None,
                cron_expression=parsed_result["cron"],
                is_built_in=False
            )
            
            # Для повторяющихся напоминаний нужна более сложная логика
            # Можно настроить планировщик на работу в часовом поясе пользователя
            if scheduler:
                await scheduler.add_recurring_reminder_with_timezone(
                    reminder_id, user_id, reminder_text, 
                    parsed_result["cron"], user['timezone']
                )
        
        await callback.message.edit_text("✅ Напоминание успешно создано!")
        await callback.message.answer(
            "Что дальше?",
            reply_markup=get_reminders_menu_keyboard()
        )
        
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при создании напоминания: {str(e)}")
        print(f"Error creating reminder: {e}")
    
    # Очищаем состояние после завершения процесса
    await state.clear()

@router.callback_query(lambda c: c.data == "reject_reminder")
async def reject_reminder(callback: types.CallbackQuery, state: FSMContext):
    """Отклонение напоминания и повторный ввод времени"""
    await callback.message.edit_text(
        "Укажите время напоминания еще раз более точно:"
    )
    await state.set_state(ReminderStates.waiting_for_time)

async def handle_reminder_action(callback: types.CallbackQuery, state: FSMContext, action: str):
    """Общая функция для обработки действий с напоминаниями"""
    # Очищаем состояние на случай, если пользователь был в процессе создания напоминания
    await state.clear()
    
    reminder_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    
    from services.scheduler_service import get_scheduler
    from services.timezone_service import convert_user_time_to_scheduler_timezone, get_scheduler_timezone
    
    scheduler = get_scheduler()
    
    if action == "delete":
        # Получаем тип напоминания перед удалением
        reminders = await db.get_user_reminders(user_id)
        reminder = next((r for r in reminders if r['reminder_id'] == reminder_id), None)
        
        if reminder:
            # Удаляем из БД используя метод с поддержкой шифрования
            success = await db.delete_reminder(reminder_id, user_id)
            
            if success:
                # Удаляем из планировщика
                if scheduler:
                    await scheduler.remove_reminder(reminder_id, reminder['reminder_type'])
                
                await callback.answer("Напоминание удалено")
            else:
                await callback.answer("Ошибка при удалении напоминания")
        else:
            await callback.answer("Напоминание не найдено")
        
    elif action == "disable":
        # Получаем тип напоминания перед отключением
        reminders = await db.get_user_reminders(user_id)
        reminder = next((r for r in reminders if r['reminder_id'] == reminder_id), None)
        
        if reminder:
            # Отключаем в БД используя метод с поддержкой шифрования
            success = await db.update_reminder_status(reminder_id, False)
            
            if success:
                # Удаляем из планировщика
                if scheduler:
                    await scheduler.remove_reminder(reminder_id, reminder['reminder_type'])
                
                await callback.answer("Напоминание отключено")
            else:
                await callback.answer("Ошибка при отключении напоминания")
        else:
            await callback.answer("Напоминание не найдено")
        
    elif action == "enable":
        # Включаем в БД используя метод с поддержкой шифрования
        success = await db.update_reminder_status(reminder_id, True)
        
        if success:
            # Получаем данные напоминания и пользователя для добавления в планировщик
            reminders = await db.get_user_reminders(user_id)
            reminder = next((r for r in reminders if r['reminder_id'] == reminder_id), None)
            
            # Получаем часовой пояс пользователя
            async with db.pool.acquire() as conn:
                user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
            
            if reminder and user and scheduler:
                if reminder['reminder_type'] == 'once':
                    # Проверяем, что время еще не прошло (сравниваем с временем пользователя)
                    user_current_time = get_user_time(user['timezone']).replace(tzinfo=None)
                    
                    if reminder['trigger_time'] > user_current_time:
                        # Преобразуем время пользователя в время планировщика
                        scheduler_trigger_time = convert_user_time_to_scheduler_timezone(
                            reminder['trigger_time'],
                            user['timezone'],
                            get_scheduler_timezone()
                        )
                        
                        await scheduler.add_once_reminder(
                            reminder_id, user_id, reminder['text'], scheduler_trigger_time
                        )
                    else:
                        # Время уже прошло, удаляем напоминание
                        await db.delete_reminder(reminder_id, user_id)
                        await callback.answer("Время напоминания уже прошло, оно было удалено")
                        await update_reminders_list_message(callback.message, user_id)
                        return
                else:
                    # Для повторяющихся напоминаний используем правильный метод
                    await scheduler.add_recurring_reminder_with_timezone(
                        reminder_id, user_id, reminder['text'], 
                        reminder['cron_expression'], user['timezone']
                    )
            
            await callback.answer("Напоминание включено")
        else:
            await callback.answer("Ошибка при включении напоминания")
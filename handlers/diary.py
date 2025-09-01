from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import logging

from database.connection import db
from keyboards.keyboards import get_diary_menu_keyboard, get_back_to_main_keyboard
from services.timezone_service import get_user_time

router = Router()
logger = logging.getLogger(__name__)

class DiaryStates(StatesGroup):
    waiting_for_entry = State()
    waiting_for_date = State()
    waiting_for_custom_date = State()
    waiting_for_period_start = State()
    waiting_for_period_end = State()
    waiting_for_edit = State()

async def _get_user_timezone(user_id: int) -> str:
    """Вспомогательная функция для получения часового пояса пользователя"""
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT timezone FROM users WHERE user_id = $1", user_id)
    timezone = user['timezone'] if user else 'UTC'
    logger.info(f"Retrieved timezone for user {user_id}: {timezone}")
    return timezone

def _parse_date_input(date_input: str) -> datetime:
    """Парсинг даты из строки с валидацией"""
    parsed_date = datetime.strptime(date_input.strip(), "%d.%m.%Y").date()
    logger.info(f"Parsed date input '{date_input}' to {parsed_date}")
    return parsed_date

def _create_date_keyboard(current_date):
    """Создание клавиатуры выбора даты"""
    yesterday = current_date - timedelta(days=1)
    logger.info(f"Creating date keyboard with current_date: {current_date}, yesterday: {yesterday}")
    
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text=f"Сегодня ({current_date.strftime('%d.%m.%Y')})", 
            callback_data=f"diary_date_{current_date.isoformat()}"
        )],
        [types.InlineKeyboardButton(
            text=f"Вчера ({yesterday.strftime('%d.%m.%Y')})", 
            callback_data=f"diary_date_{yesterday.isoformat()}"
        )],
        [types.InlineKeyboardButton(
            text="📅 Выбрать другую дату", 
            callback_data="diary_custom_date"
        )]
    ])

def _create_view_menu_keyboard(current_date):
    """Создание клавиатуры меню просмотра"""
    logger.info(f"Creating view menu keyboard with current_date: {current_date}")
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Сегодня", callback_data=f"view_diary_{current_date.isoformat()}")],
        [types.InlineKeyboardButton(text="Вчера", callback_data=f"view_diary_{(current_date - timedelta(days=1)).isoformat()}")],
        [types.InlineKeyboardButton(text="📅 Своя дата", callback_data="view_diary_custom")],
        [types.InlineKeyboardButton(text="📊 Выбрать период", callback_data="view_diary_period")]
    ])

def _decrypt_entry_safely(entry_content: str, entry_id: int) -> str:
    """Безопасная расшифровка записи с обработкой ошибок"""
    try:
        from services.encryption_service import decrypt_text
        decrypted = decrypt_text(entry_content)
        logger.debug(f"Successfully decrypted entry {entry_id}")
        return decrypted
    except Exception as e:
        logger.error(f"Failed to decrypt entry {entry_id}: {e}")
        return "[Ошибка расшифровки]"

async def _save_diary_entry(user_id: int, target_date, entry_text: str, message: types.Message):
    """Сохранение записи в дневник с обработкой ошибок"""
    try:
        logger.info(f"Attempting to save diary entry for user {user_id} on date {target_date}")
        logger.debug(f"Entry text length: {len(entry_text)}")
        
        entry_id = await db.create_diary_entry(user_id, target_date, entry_text)
        logger.info(f"Successfully created diary entry {entry_id} for user {user_id} on {target_date}")
        return True, entry_id
    except Exception as e:
        logger.error(f"Failed to create diary entry for user {user_id} on {target_date}: {e}")
        return False, None

@router.message(lambda message: message.text == "✍️ Новая запись")
async def create_diary_entry(message: types.Message, state: FSMContext):
    """Создание новой записи в дневнике"""
    logger.info(f"User {message.from_user.id} started creating new diary entry")
    # Очищаем состояние перед началом нового процесса
    await state.clear()
    
    await message.answer(
        "✍️ Новая запись в дневнике\n\n"
        "Введите текст записи:",
        reply_markup=get_back_to_main_keyboard()
    )
    await state.set_state(DiaryStates.waiting_for_entry)

@router.message(DiaryStates.waiting_for_entry)
async def process_diary_entry(message: types.Message, state: FSMContext):
    """Обработка текста записи"""
    if message.text == "🏠 Главное меню":
        logger.info(f"User {message.from_user.id} cancelled diary entry creation")
        await state.clear()
        return
    
    entry_text = message.text
    logger.info(f"User {message.from_user.id} entered diary text with length {len(entry_text)}")
    await state.update_data(entry_text=entry_text)
    
    user_timezone = await _get_user_timezone(message.from_user.id)
    current_time = get_user_time(user_timezone)
    logger.info(f"Current time for user {message.from_user.id} in timezone {user_timezone}: {current_time}")
    
    keyboard = _create_date_keyboard(current_time.date())
    
    await message.answer(
        f"✅ Текст записи: {entry_text[:100]}{'...' if len(entry_text) > 100 else ''}\n\n"
        "Выберите дату для записи:",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data == "diary_custom_date")
async def ask_custom_date(callback: types.CallbackQuery, state: FSMContext):
    """Запрос пользовательской даты"""
    logger.info(f"User {callback.from_user.id} requested custom date input")
    await callback.message.edit_text(
        "📅 Введите дату в формате ДД.ММ.ГГГГ (например, 25.02.2025):"
    )
    await state.set_state(DiaryStates.waiting_for_custom_date)

@router.message(DiaryStates.waiting_for_custom_date)
async def process_custom_date(message: types.Message, state: FSMContext):
    """Обработка пользовательской даты"""
    try:
        target_date = _parse_date_input(message.text)
        logger.info(f"User {message.from_user.id} entered custom date: {target_date}")
        
        # Проверяем, что дата не в будущем
        user_timezone = await _get_user_timezone(message.from_user.id)
        current_date = get_user_time(user_timezone).date()
        logger.info(f"Comparing target_date {target_date} with current_date {current_date} for user {message.from_user.id}")
        
        if target_date > current_date:
            logger.warning(f"User {message.from_user.id} tried to create entry for future date: {target_date}")
            await message.answer(
                "❌ Нельзя создавать записи на будущие даты. Введите прошедшую дату или сегодняшнюю:"
            )
            return
        
        # Сохраняем запись
        data = await state.get_data()
        success, entry_id = await _save_diary_entry(
            message.from_user.id, target_date, data.get("entry_text"), message
        )
        
        if success:
            logger.info(f"Successfully saved diary entry {entry_id} for user {message.from_user.id}")
            await message.answer(
                f"✅ Запись добавлена в дневник!\n\n"
                f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n"
                f"📝 Запись: {data.get('entry_text')[:200]}{'...' if len(data.get('entry_text', '')) > 200 else ''}"
            )
            await message.answer("Что дальше?", reply_markup=get_diary_menu_keyboard())
            await state.clear()
        else:
            logger.error(f"Failed to save diary entry for user {message.from_user.id}")
            await message.answer("❌ Произошла ошибка при сохранении записи. Попробуйте еще раз.")
    
    except ValueError as e:
        logger.warning(f"User {message.from_user.id} entered invalid date format: {message.text}")
        await message.answer(
            "❌ Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например, 25.02.2025):"
        )

@router.callback_query(lambda c: c.data.startswith("diary_date_"))
async def process_diary_date(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбранной даты для записи"""
    date_str = callback.data.replace("diary_date_", "")
    target_date = datetime.fromisoformat(date_str).date()
    logger.info(f"User {callback.from_user.id} selected date from keyboard: {target_date}")
    
    data = await state.get_data()
    success, entry_id = await _save_diary_entry(
        callback.from_user.id, target_date, data.get("entry_text"), callback.message
    )
    
    if success:
        logger.info(f"Successfully saved diary entry {entry_id} for user {callback.from_user.id}")
        await callback.message.edit_text(
            f"✅ Запись добавлена в дневник!\n\n"
            f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n"
            f"📝 Запись: {data.get('entry_text')[:200]}{'...' if len(data.get('entry_text', '')) > 200 else ''}"
        )
        await callback.message.answer("Что дальше?", reply_markup=get_diary_menu_keyboard())
        await state.clear()
    else:
        logger.error(f"Failed to save diary entry for user {callback.from_user.id}")
        await callback.message.edit_text("❌ Произошла ошибка при сохранении записи. Попробуйте еще раз.")

@router.message(lambda message: message.text == "📖 Просмотр записей")
async def view_diary_entries(message: types.Message, state: FSMContext):
    """Просмотр записей дневника"""
    logger.info(f"User {message.from_user.id} requested diary entries view")
    # Очищаем состояние перед просмотром
    await state.clear()
    
    user_timezone = await _get_user_timezone(message.from_user.id)
    current_date = get_user_time(user_timezone).date()
    logger.info(f"Current date for viewing for user {message.from_user.id}: {current_date}")
    
    keyboard = _create_view_menu_keyboard(current_date)
    
    await message.answer(
        "📖 Просмотр записей дневника\n\n"
        "Выберите период:",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data == "view_diary_custom")
async def ask_view_date(callback: types.CallbackQuery, state: FSMContext):
    """Запрос даты для просмотра"""
    logger.info(f"User {callback.from_user.id} requested custom date for viewing")
    await callback.message.edit_text(
        "📅 Введите дату для просмотра в формате ДД.ММ.ГГГГ (например, 25.02.2025):"
    )
    await state.set_state(DiaryStates.waiting_for_date)

@router.message(DiaryStates.waiting_for_date)
async def process_view_date(message: types.Message, state: FSMContext):
    """Обработка даты для просмотра"""
    try:
        target_date = _parse_date_input(message.text)
        logger.info(f"User {message.from_user.id} requested to view entries for date: {target_date}")
        await show_entries_for_date(message, target_date, user_id=message.from_user.id)
        await state.clear()
    except ValueError:
        logger.warning(f"User {message.from_user.id} entered invalid date format for viewing: {message.text}")
        await message.answer(
            "❌ Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например, 25.02.2025):"
        )

@router.callback_query(lambda c: c.data == "view_diary_period")
async def ask_period_start(callback: types.CallbackQuery, state: FSMContext):
    """Запрос начальной даты периода"""
    logger.info(f"User {callback.from_user.id} requested period view")
    await callback.message.edit_text(
        "📅 Введите начальную дату периода в формате ДД.ММ.ГГГГ (например, 20.02.2025):"
    )
    await state.set_state(DiaryStates.waiting_for_period_start)

@router.message(DiaryStates.waiting_for_period_start)
async def process_period_start(message: types.Message, state: FSMContext):
    """Обработка начальной даты периода"""
    try:
        start_date = _parse_date_input(message.text)
        logger.info(f"User {message.from_user.id} entered period start date: {start_date}")
        await state.update_data(start_date=start_date)
        
        await message.answer(
            f"✅ Начальная дата: {start_date.strftime('%d.%m.%Y')}\n\n"
            "Введите конечную дату периода в формате ДД.ММ.ГГГГ:"
        )
        await state.set_state(DiaryStates.waiting_for_period_end)
    except ValueError:
        logger.warning(f"User {message.from_user.id} entered invalid start date format: {message.text}")
        await message.answer(
            "❌ Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например, 20.02.2025):"
        )

@router.message(DiaryStates.waiting_for_period_end)
async def process_period_end(message: types.Message, state: FSMContext):
    """Обработка конечной даты периода"""
    try:
        end_date = _parse_date_input(message.text)
        data = await state.get_data()
        start_date = data.get("start_date")
        logger.info(f"User {message.from_user.id} entered period end date: {end_date}, start_date: {start_date}")
        
        if end_date < start_date:
            logger.warning(f"User {message.from_user.id} entered end date before start date")
            await message.answer(
                "❌ Конечная дата не может быть раньше начальной. Введите корректную конечную дату:"
            )
            return
        
        await show_entries_for_period(message, start_date, end_date, user_id=message.from_user.id)
        await state.clear()
    except ValueError:
        logger.warning(f"User {message.from_user.id} entered invalid end date format: {message.text}")
        await message.answer(
            "❌ Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например, 25.02.2025):"
        )

@router.callback_query(lambda c: c.data.startswith("view_diary_2"))
async def show_diary_entries(callback: types.CallbackQuery):
    """Показать записи дневника за конкретный день"""
    date_str = callback.data.replace("view_diary_", "")
    target_date = datetime.fromisoformat(date_str).date()
    logger.info(f"User {callback.from_user.id} selected quick view for date: {target_date}")
    
    await show_entries_for_date(callback.message, target_date, edit_message=True, user_id=callback.from_user.id)

def _create_entry_keyboard(entries):
    """Создание клавиатуры для управления записями"""
    keyboard_buttons = []
    logger.debug(f"Creating entry keyboard for {len(entries)} entries")
    
    for i, entry in enumerate(entries, 1):
        row = [
            types.InlineKeyboardButton(
                text=f"✏️ Редактировать #{i}", 
                callback_data=f"edit_entry_{entry['entry_id']}"
            ),
            types.InlineKeyboardButton(
                text=f"🗑️ Удалить #{i}", 
                callback_data=f"delete_entry_{entry['entry_id']}"
            )
        ]
        keyboard_buttons.append(row)
    
    keyboard_buttons.append([
        types.InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_view_menu")
    ])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

async def show_entries_for_date(message: types.Message, target_date, edit_message=False, user_id=None):
    """Показать записи за конкретную дату с кнопками управления"""
    if user_id is None:
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    
    logger.info(f"Showing entries for user {user_id} on date {target_date}")
    
    try:
        entries = await db.get_diary_entries_by_date(user_id, target_date)
        logger.info(f"Retrieved {len(entries)} diary entries for user {user_id} on {target_date}")
        
        # Подробное логирование каждой записи
        for i, entry in enumerate(entries):
            logger.debug(f"Entry {i+1} for user {user_id}: "
                        f"entry_id={entry.get('entry_id')}, "
                        f"created_at={entry.get('created_at')} (type: {type(entry.get('created_at'))}), "
                        f"entry_date={entry.get('entry_date')} (type: {type(entry.get('entry_date'))}), "
                        f"is_edited={entry.get('is_edited')}")
            
            # Если created_at - это datetime объект, логируем его timezone info
            created_at = entry.get('created_at')
            if isinstance(created_at, datetime):
                logger.debug(f"Entry {i+1} created_at timezone info: {created_at.tzinfo}")
                logger.debug(f"Entry {i+1} created_at UTC offset: {created_at.utctimetuple()}")
        
    except Exception as e:
        logger.error(f"Failed to retrieve diary entries for user {user_id} on {target_date}: {e}")
        await message.answer("❌ Произошла ошибка при получении записей")
        return
    
    if not entries:
        logger.info(f"No entries found for user {user_id} on {target_date}")
        text = f"📖 За {target_date.strftime('%d.%m.%Y')} записей нет"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_view_menu")]
        ])
        
        if edit_message:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)
        return
    
    # Формируем текст с записями
    text = f"📖 Записи за {target_date.strftime('%d.%m.%Y')}:\n\n"
    
    for i, entry in enumerate(entries, 1):
        edited_mark = " (edited)" if entry['is_edited'] else ""
        created_at = entry['created_at']
        
        # Подробное логирование обработки времени
        logger.debug(f"Processing entry {i} time: {created_at} (type: {type(created_at)})")
        
        if isinstance(created_at, datetime):
            time_str = created_at.strftime('%H:%M')
            created_date = created_at.date()
            logger.debug(f"Entry {i} - time_str: {time_str}, created_date: {created_date}")
        else:
            logger.warning(f"Entry {i} - created_at is not datetime object: {created_at}")
            time_str = str(created_at)
            created_date = target_date  # fallback
        
        # Определяем информацию о дате
        if created_date != target_date:
            date_info = f"📅 {created_date.strftime('%d.%m.%Y')} в {time_str}"
            logger.debug(f"Entry {i} created on different date: {created_date} vs {target_date}")
        else:
            date_info = f"🕐 {time_str}"
            logger.debug(f"Entry {i} created on same date")
        
        text += f"{i}. {entry['content']}\n{date_info}{edited_mark}\n\n"
    
    keyboard = _create_entry_keyboard(entries)
    
    if edit_message:
        await message.edit_text(text[:4000], reply_markup=keyboard)
    else:
        await message.answer(text[:4000], reply_markup=keyboard)

async def show_entries_for_period(message: types.Message, start_date, end_date, user_id=None):
    """Показать записи за период"""
    if user_id is None:
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    
    logger.info(f"Showing entries for user {user_id} for period {start_date} - {end_date}")
    
    try:
        entries = await db.get_diary_entries_by_period(user_id, start_date, end_date)
        logger.info(f"Retrieved {len(entries)} diary entries for user {user_id} for period {start_date} - {end_date}")
        
        # Логирование записей за период
        for i, entry in enumerate(entries):
            logger.debug(f"Period entry {i+1}: "
                        f"entry_id={entry.get('entry_id')}, "
                        f"created_at={entry.get('created_at')}, "
                        f"entry_date={entry.get('entry_date')}")
            
    except Exception as e:
        logger.error(f"Failed to retrieve diary entries for user {user_id} for period {start_date} - {end_date}: {e}")
        await message.answer("❌ Произошла ошибка при получении записей")
        return
    
    if not entries:
        await message.answer(
            f"📖 За период с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')} записей нет"
        )
        return
    
    # Формируем текст с записями по датам
    text = f"📖 Записи за период {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}:\n\n"
    
    current_date = None
    for entry in entries:
        if current_date != entry['entry_date']:
            current_date = entry['entry_date']
            text += f"\n📅 {current_date.strftime('%d.%m.%Y')}\n"
        
        edited_mark = " (edited)" if entry['is_edited'] else ""
        created_at = entry['created_at']
        
        if isinstance(created_at, datetime):
            time_str = created_at.strftime('%H:%M')
            created_date = created_at.date()
        else:
            time_str = str(created_at)
            created_date = entry['entry_date']
        
        entry_date = entry['entry_date']
        
        # Информация о времени создания
        if created_date != entry_date:
            time_info = f"{time_str} (создано {created_date.strftime('%d.%m.%Y')})"
        else:
            time_info = time_str
        
        text += f"• {entry['content']} - {time_info}{edited_mark}\n"
    
    # Разбиваем длинный текст на части
    if len(text) > 4000:
        parts = []
        current_part = ""
        
        for line in text.split('\n'):
            if len(current_part + line + '\n') > 4000:
                parts.append(current_part)
                current_part = line + '\n'
            else:
                current_part += line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(text)

@router.callback_query(lambda c: c.data == "back_to_view_menu")
async def back_to_view_menu(callback: types.CallbackQuery):
    """Возврат к меню просмотра"""
    logger.info(f"User {callback.from_user.id} returned to view menu")
    user_timezone = await _get_user_timezone(callback.from_user.id)
    current_date = get_user_time(user_timezone).date()
    keyboard = _create_view_menu_keyboard(current_date)
    
    await callback.message.edit_text(
        "📖 Просмотр записей дневника\n\n"
        "Выберите период:",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data.startswith("edit_entry_"))
async def edit_entry(callback: types.CallbackQuery, state: FSMContext):
    """Редактирование записи"""
    entry_id = int(callback.data.replace("edit_entry_", ""))
    user_id = callback.from_user.id
    logger.info(f"User {user_id} started editing entry {entry_id}")
    
    # Получаем запись для редактирования
    async with db.pool.acquire() as conn:
        entry = await conn.fetchrow(
            "SELECT content FROM diary_entries WHERE entry_id = $1 AND user_id = $2",
            entry_id, user_id
        )
    
    if not entry:
        logger.warning(f"Entry {entry_id} not found for user {user_id}")
        await callback.answer("Запись не найдена", show_alert=True)
        return
    
    decrypted_content = _decrypt_entry_safely(entry['content'], entry_id)
    if decrypted_content == "[Ошибка расшифровки]":
        await callback.answer("❌ Ошибка при расшифровке записи", show_alert=True)
        return
    
    await state.update_data(edit_entry_id=entry_id)
    await callback.message.edit_text(
        f"✏️ Редактирование записи\n\n"
        f"Текущий текст: {decrypted_content}\n\n"
        f"Введите новый текст записи:"
    )
    await state.set_state(DiaryStates.waiting_for_edit)

@router.message(DiaryStates.waiting_for_edit)
async def process_edit(message: types.Message, state: FSMContext):
    """Обработка редактирования записи"""
    new_content = message.text
    data = await state.get_data()
    entry_id = data.get("edit_entry_id")
    user_id = message.from_user.id
    
    try:
        success = await db.update_diary_entry(entry_id, user_id, new_content)
        
        if success:
            entry_date = await db.get_diary_entry_date(entry_id)
            await message.answer("✅ Запись успешно обновлена!")
            await show_entries_for_date(message, entry_date, user_id=user_id)
            await state.clear()
        else:
            await message.answer("❌ Не удалось обновить запись")
    except Exception as e:
        logger.error(f"Failed to update diary entry {entry_id}: {e}")
        await message.answer("❌ Произошла ошибка при обновлении записи")

@router.callback_query(lambda c: c.data.startswith("delete_entry_"))
async def delete_entry_confirm(callback: types.CallbackQuery):
    """Подтверждение удаления записи"""
    entry_id = int(callback.data.replace("delete_entry_", ""))
    user_id = callback.from_user.id
    
    # Получаем информацию о записи
    async with db.pool.acquire() as conn:
        entry = await conn.fetchrow(
            "SELECT content, entry_date FROM diary_entries WHERE entry_id = $1 AND user_id = $2",
            entry_id, user_id
        )
    
    if not entry:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    
    decrypted_content = _decrypt_entry_safely(entry['content'], entry_id)
    
    # Клавиатура подтверждения
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{entry_id}"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_delete_{entry_id}")
        ]
    ])
    
    await callback.message.edit_text(
        f"🗑️ Удаление записи\n\n"
        f"Вы уверены, что хотите удалить эту запись?\n\n"
        f"📝 {decrypted_content[:100]}{'...' if len(decrypted_content) > 100 else ''}\n\n"
        f"⚠️ Это действие нельзя отменить!",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data.startswith("confirm_delete_"))
async def delete_entry(callback: types.CallbackQuery):
    """Удаление записи"""
    entry_id = int(callback.data.replace("confirm_delete_", ""))
    user_id = callback.from_user.id
    
    try:
        entry_date = await db.get_diary_entry_date(entry_id)
        
        if entry_date:
            success = await db.delete_diary_entry(entry_id, user_id)
            
            if success:
                await callback.message.edit_text("✅ Запись удалена!")
                await show_entries_for_date(callback.message, entry_date, user_id=user_id)
            else:
                await callback.answer("❌ Не удалось удалить запись", show_alert=True)
        else:
            await callback.answer("Запись не найдена", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to delete diary entry {entry_id}: {e}")
        await callback.answer("❌ Произошла ошибка при удалении", show_alert=True)

@router.callback_query(lambda c: c.data.startswith("cancel_delete_"))
async def cancel_delete(callback: types.CallbackQuery):
    """Отмена удаления записи"""
    entry_id = int(callback.data.replace("cancel_delete_", ""))
    user_id = callback.from_user.id
    
    try:
        entry_date = await db.get_diary_entry_date(entry_id)
        
        if entry_date:
            await show_entries_for_date(callback.message, entry_date, edit_message=True, user_id=user_id)
        else:
            await callback.answer("Запись не найдена", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to get diary entry date {entry_id}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

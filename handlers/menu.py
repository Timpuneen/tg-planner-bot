from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from keyboards.keyboards import (
    get_main_menu_keyboard,
    get_reminders_menu_keyboard,
    get_tasks_menu_keyboard,
    get_diary_menu_keyboard,
    get_timezone_keyboard
)

router = Router()

@router.message(lambda message: message.text in ["🏠 Главное меню"])
async def main_menu(message: types.Message, state: FSMContext):
    """Возврат в главное меню"""
    # Очищаем любое активное состояние
    await state.clear()
    
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(lambda message: message.text == "⏰ Напоминания")
async def reminders_menu(message: types.Message, state: FSMContext):
    """Меню напоминаний"""
    # Очищаем состояние перед входом в новый раздел
    await state.clear()
    
    await message.answer(
        "📋 Напоминания\n\n"
        "Выберите действие:",
        reply_markup=get_reminders_menu_keyboard()
    )

@router.message(lambda message: message.text == "📝 Задачи")
async def tasks_menu(message: types.Message, state: FSMContext):
    """Меню задач"""
    # Очищаем состояние перед входом в новый раздел
    await state.clear()
    
    await message.answer(
        "📝 Задачи\n\n"
        "Выберите действие:",
        reply_markup=get_tasks_menu_keyboard()
    )

@router.message(lambda message: message.text == "📔 Дневник")
async def diary_menu(message: types.Message, state: FSMContext):
    """Меню дневника"""
    # Очищаем состояние перед входом в новый раздел
    await state.clear()
    
    await message.answer(
        "📔 Дневник\n\n"
        "Выберите действие:",
        reply_markup=get_diary_menu_keyboard()
    )

@router.message(lambda message: message.text == "🌍 Изменить часовой пояс")
async def change_timezone_menu(message: types.Message, state: FSMContext):
    """Изменение часового пояса"""
    # Очищаем состояние перед входом в новый раздел
    await state.clear()
    
    await message.answer(
        "🌍 Изменение часового пояса\n\n"
        "Выберите новый часовой пояс:",
        reply_markup=get_timezone_keyboard()
    )
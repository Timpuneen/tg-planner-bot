from aiogram import Router, types
from keyboards.keyboards import (
    get_main_menu_keyboard,
    get_reminders_menu_keyboard,
    get_tasks_menu_keyboard,
    get_diary_menu_keyboard,
    get_timezone_keyboard
)

router = Router()

@router.message(lambda message: message.text in ["🏠 Главное меню"])
async def main_menu(message: types.Message):
    """Возврат в главное меню"""
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(lambda message: message.text == "⏰ Напоминания")
async def reminders_menu(message: types.Message):
    """Меню напоминаний"""
    await message.answer(
        "📋 Напоминания\n\n"
        "Выберите действие:",
        reply_markup=get_reminders_menu_keyboard()
    )

@router.message(lambda message: message.text == "📝 Задачи")
async def tasks_menu(message: types.Message):
    """Меню задач"""
    await message.answer(
        "📝 Задачи\n\n"
        "Выберите действие:",
        reply_markup=get_tasks_menu_keyboard()
    )

@router.message(lambda message: message.text == "📔 Дневник")
async def diary_menu(message: types.Message):
    """Меню дневника"""
    await message.answer(
        "📔 Дневник\n\n"
        "Выберите действие:",
        reply_markup=get_diary_menu_keyboard()
    )

@router.message(lambda message: message.text == "🌍 Изменить часовой пояс")
async def change_timezone_menu(message: types.Message):
    """Изменение часового пояса"""
    await message.answer(
        "🌍 Изменение часового пояса\n\n"
        "Выберите новый часовой пояс:",
        reply_markup=get_timezone_keyboard()
    )
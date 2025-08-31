import pytz
import os
from geopy.geocoders import Nominatim
from datetime import datetime
from typing import Optional

async def get_timezone_from_location(latitude: float, longitude: float) -> str:
    """
    Определяет часовой пояс по координатам
    Простая реализация - в реальном проекте лучше использовать специальный сервис
    """
    try:
        # Для упрощения используем приблизительное определение по координатам
        # В реальном проекте лучше использовать API типа TimeZoneDB
        
        if 55 <= latitude <= 70 and 37 <= longitude <= 170:
            return "Europe/Moscow"
        elif 40 <= latitude <= 52 and 22 <= longitude <= 40:
            return "Europe/Kiev"
        elif 43 <= latitude <= 55 and 51 <= longitude <= 87:
            return "Asia/Almaty"
        elif 49 <= latitude <= 61 and -8 <= longitude <= 2:
            return "Europe/London"
        elif 47 <= latitude <= 55 and 6 <= longitude <= 15:
            return "Europe/Berlin"
        elif 25 <= latitude <= 49 and -125 <= longitude <= -66:
            return "America/New_York"
        elif 32 <= latitude <= 49 and -125 <= longitude <= -114:
            return "America/Los_Angeles"
        elif 24 <= latitude <= 46 and 123 <= longitude <= 146:
            return "Asia/Tokyo"
        elif -44 <= latitude <= -10 and 113 <= longitude <= 154:
            return "Australia/Sydney"
        else:
            return "UTC"
    
    except Exception:
        return "UTC"

def get_user_time(timezone_str: str) -> datetime:
    """Получает текущее время в часовом поясе пользователя"""
    try:
        tz = pytz.timezone(timezone_str)
        return datetime.now(tz)
    except Exception:
        return datetime.now(pytz.UTC)

def convert_user_time_to_scheduler_timezone(
    user_datetime: datetime, 
    user_timezone: str, 
    scheduler_timezone: Optional[str] = None
) -> datetime:
    """
    Преобразует время из часового пояса пользователя в часовой пояс планировщика
    
    Args:
        user_datetime: datetime объект (наивный, в часовом поясе пользователя)
        user_timezone: строка часового пояса пользователя (например, 'Europe/Moscow')
        scheduler_timezone: строка часового пояса планировщика (если None, берется из get_scheduler_timezone())
    
    Returns:
        datetime объект в часовом поясе планировщика (наивный)
    """
    try:
        if scheduler_timezone is None:
            scheduler_timezone = get_scheduler_timezone()
            
        # Получаем объекты часовых поясов
        user_tz = pytz.timezone(user_timezone)
        scheduler_tz = pytz.timezone(scheduler_timezone)
        
        # Локализуем время пользователя (делаем aware)
        user_aware_time = user_tz.localize(user_datetime)
        
        # Конвертируем в часовой пояс планировщика
        scheduler_time = user_aware_time.astimezone(scheduler_tz)
        
        # Возвращаем наивное время в часовом поясе планировщика
        return scheduler_time.replace(tzinfo=None)
        
    except Exception as e:
        print(f"Error converting timezone from {user_timezone} to {scheduler_timezone}: {e}")
        # В случае ошибки возвращаем исходное время
        return user_datetime

def convert_scheduler_time_to_user_timezone(
    scheduler_datetime: datetime,
    user_timezone: str,
    scheduler_timezone: Optional[str] = None
) -> datetime:
    """
    Преобразует время из часового пояса планировщика в часовой пояс пользователя
    
    Args:
        scheduler_datetime: datetime объект (наивный, в часовом поясе планировщика)
        user_timezone: строка часового пояса пользователя
        scheduler_timezone: строка часового пояса планировщика (если None, берется из get_scheduler_timezone())
    
    Returns:
        datetime объект в часовом поясе пользователя (наивный)
    """
    try:
        if scheduler_timezone is None:
            scheduler_timezone = get_scheduler_timezone()
            
        # Получаем объекты часовых поясов
        scheduler_tz = pytz.timezone(scheduler_timezone)
        user_tz = pytz.timezone(user_timezone)
        
        # Локализуем время планировщика (делаем aware)
        scheduler_aware_time = scheduler_tz.localize(scheduler_datetime)
        
        # Конвертируем в часовой пояс пользователя
        user_time = scheduler_aware_time.astimezone(user_tz)
        
        # Возвращаем наивное время в часовом поясе пользователя
        return user_time.replace(tzinfo=None)
        
    except Exception as e:
        print(f"Error converting timezone from {scheduler_timezone} to {user_timezone}: {e}")
        # В случае ошибки возвращаем исходное время
        return scheduler_datetime

def get_scheduler_timezone() -> str:
    """
    Возвращает часовой пояс планировщика
    Можно настроить через переменную окружения или конфиг
    """
    return os.getenv('SCHEDULER_TIMEZONE', 'UTC')

def get_timezone_offset_hours(timezone_str: str) -> int:
    """
    Получает смещение часового пояса в часах относительно UTC
    
    Args:
        timezone_str: строка часового пояса (например, 'Europe/Moscow')
    
    Returns:
        int: смещение в часах (например, +3 для Москвы)
    """
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        return int(now.utcoffset().total_seconds() / 3600)
    except Exception:
        return 0

def is_valid_timezone(timezone_str: str) -> bool:
    """
    Проверяет, является ли строка валидным часовым поясом
    
    Args:
        timezone_str: строка часового пояса для проверки
    
    Returns:
        bool: True если валидный, False если нет
    """
    try:
        pytz.timezone(timezone_str)
        return True
    except pytz.exceptions.UnknownTimeZoneError:
        return False

def format_timezone_name(timezone_str: str) -> str:
    """
    Форматирует название часового пояса для отображения пользователю
    
    Args:
        timezone_str: строка часового пояса (например, 'Europe/Moscow')
    
    Returns:
        str: отформатированное название (например, 'Москва (UTC+3)')
    """
    timezone_names = {
        'Europe/Moscow': 'Москва',
        'Europe/Kiev': 'Киев', 
        'Asia/Almaty': 'Алматы',
        'Europe/London': 'Лондон',
        'Europe/Berlin': 'Берлин',
        'America/New_York': 'Нью-Йорк',
        'America/Los_Angeles': 'Лос-Анджелес',
        'Asia/Tokyo': 'Токио',
        'Australia/Sydney': 'Сидней',
        'UTC': 'UTC'
    }
    
    try:
        offset = get_timezone_offset_hours(timezone_str)
        offset_str = f"UTC{offset:+d}" if offset != 0 else "UTC"
        
        city_name = timezone_names.get(timezone_str, timezone_str.split('/')[-1])
        return f"{city_name} ({offset_str})"
        
    except Exception:
        return timezone_str

# Пример использования и тестирования:
if __name__ == "__main__":
    # Тест конвертации времени
    user_time = datetime(2024, 1, 15, 13, 0)  # 13:00
    print(f"Время пользователя (Москва): {user_time}")
    
    # Конвертируем в часовой пояс планировщика (Алматы)
    scheduler_time = convert_user_time_to_scheduler_timezone(
        user_time, 
        'Europe/Moscow',  # пользователь +3
        'Asia/Almaty'     # планировщик +6
    )
    print(f"Время планировщика (Алматы): {scheduler_time}")
    
    # Проверяем обратную конвертацию
    back_to_user = convert_scheduler_time_to_user_timezone(
        scheduler_time,
        'Europe/Moscow',
        'Asia/Almaty'
    )
    print(f"Обратно во время пользователя: {back_to_user}")
    
    # Тест форматирования
    print(f"Форматированное название: {format_timezone_name('Europe/Moscow')}")
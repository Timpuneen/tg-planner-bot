from openai import AsyncOpenAI
from config import OPENAI_API_KEY
import logging

logger = logging.getLogger(__name__)

# Настройка OpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def parse_reminder_time(user_input: str, current_time: str, timezone: str, reminder_type: str):
    """
    Парсит пользовательский ввод времени напоминания через OpenAI
    """
    try:
        if reminder_type == "once":
            system_prompt = f"""
            Ты помощник для парсинга времени разовых напоминаний. 
            Текущее время: {current_time}
            Часовой пояс пользователя: {timezone}

            Преобразуй пользовательский ввод в JSON формат:
            
            {{
                "type": "once",
                "datetime": "YYYY-MM-DD HH:MM:SS",
                "success": true
            }}
            
            Если не удалось составить, верни:
            {{
                "success": false,
                "error": "описание проблемы"
            }}
            
            Будь внимателен если пользователь указывает время в секундах.
            Важно: возвращай ТОЛЬКО JSON, без дополнительного текста.
            """
        else:  # recurring
            system_prompt = f"""
            Ты помощник для парсинга времени повторяющихся напоминаний. 
            Текущее время: {current_time}
            Часовой пояс пользователя: {timezone}

            Преобразуй пользовательский ввод в JSON формат:
            
            {{
                "type": "recurring",
                "cron": "* * * * *",
                "description": "описание расписания",  //строгое описание, переводи секунды в минуты
                "success": true
            }}
            
            Если не удалось составить, верни:
            {{
                "success": false,
                "error": "описание проблемы"
            }}
            
            Будь внимателен если пользователь указывает время в секундах. Конвертируй при необходимости.
            Важно: возвращай ТОЛЬКО JSON, без дополнительного текста.
            """
        
        user_prompt = f"Пользователь хочет создать напоминание: {user_input}"
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=200
        )
        

        result_text = response.choices[0].message.content.strip()
        
        # Пытаемся извлечь JSON из ответа
        if result_text.startswith('```json'):
            result_text = result_text[7:]
        if result_text.endswith('```'):
            result_text = result_text[:-3]
        
        import json
        result = json.loads(result_text)
        return result
        
    except Exception as e:
        logger.error(f"Error parsing reminder time with OpenAI: {e}")
        return {
            "success": False,
            "error": f"Ошибка обработки: {str(e)}"
        }

async def generate_daily_motivation():
    """Генерирует мотивационное сообщение на день"""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system", 
                    "content": "Ты помощник, который создает короткие мотивационные сообщения на день. Сообщение должно быть вдохновляющим, но не банальным и не длиннее 100 символов."
                },
                {
                    "role": "user", 
                    "content": "Создай мотивационное сообщение на новый день"
                }
            ],
            temperature=0.7,
            max_tokens=50
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        logger.error(f"Error generating daily motivation: {e}")
        return "Доброе утро! Сегодня отличный день для новых свершений! 🌟"
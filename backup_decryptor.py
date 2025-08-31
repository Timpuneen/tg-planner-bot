#!/usr/bin/env python3
"""
Автономный декриптор бэкапов для TG-PLANNER-BOT
Работает независимо от основного проекта
"""

import json
import gzip
import logging
import base64
import argparse
import os
import sys
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

# Криптографические импорты
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    print("ERROR: cryptography library not found!")
    print("Install it with: pip install cryptography")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('backup_decryptor.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class StandaloneEncryptionService:
    """Автономный сервис шифрования"""
    
    def __init__(self, master_key: str):
        """
        Инициализация сервиса шифрования
        
        Args:
            master_key: Мастер-ключ для расшифровки
        """
        if not master_key:
            raise ValueError("Master key is required for decryption")
        
        self.fernet = self._create_fernet_key(master_key)
        logger.info("Encryption service initialized")
    
    def _create_fernet_key(self, master_key: str) -> Fernet:
        """
        Создание ключа Fernet из мастер-ключа
        
        Args:
            master_key: Мастер-ключ в виде строки
            
        Returns:
            Объект Fernet для расшифровки
        """
        # Используем ту же соль, что и в оригинале
        salt = b'salt_for_tg_planner_bot_2024'
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return Fernet(key)
    
    def decrypt(self, encrypted_data: str) -> str:
        """
        Расшифровка строки
        
        Args:
            encrypted_data: Зашифрованная строка в base64
            
        Returns:
            Расшифрованная строка
        """
        if not encrypted_data:
            return ""
        
        try:
            decoded_data = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted_data = self.fernet.decrypt(decoded_data)
            return decrypted_data.decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise


class StandaloneBackupDecryptor:
    """Автономная утилита для расшифровки зашифрованных бэкапов"""
    
    def __init__(self, encryption_key: str):
        """
        Инициализация декриптора
        
        Args:
            encryption_key: Ключ шифрования
        """
        self.encryption_service = StandaloneEncryptionService(encryption_key)
        logger.info("Standalone backup decryptor initialized")
    
    def _decrypt_safely(self, encrypted_text: str, item_type: str, item_id: Any = None) -> str:
        """Безопасная расшифровка с обработкой ошибок"""
        try:
            return self.encryption_service.decrypt(encrypted_text)
        except Exception as e:
            error_msg = f"[Ошибка расшифровки {item_type}]"
            logger.error(f"Failed to decrypt {item_type} {item_id}: {e}")
            return error_msg
    
    def load_backup_from_file(self, file_path: str) -> Dict[str, Any]:
        """Загрузка бэкапа из сжатого файла"""
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Backup file not found: {file_path}")
        
        try:
            with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            logger.info(f"Backup loaded from {file_path}")
            return backup_data
            
        except Exception as e:
            logger.error(f"Error loading backup from {file_path}: {e}")
            raise
    
    def load_backup_from_bytes(self, compressed_data: bytes) -> Dict[str, Any]:
        """Загрузка бэкапа из сжатых байтов"""
        try:
            decompressed_data = gzip.decompress(compressed_data)
            backup_data = json.loads(decompressed_data.decode('utf-8'))
            
            logger.info("Backup loaded from bytes")
            return backup_data
            
        except Exception as e:
            logger.error(f"Error loading backup from bytes: {e}")
            raise
    
    def decrypt_tasks(self, encrypted_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Расшифровка задач"""
        decrypted_tasks = []
        for task in encrypted_tasks:
            task_copy = task.copy()
            if 'text' in task_copy and task_copy['text']:
                task_copy['text'] = self._decrypt_safely(
                    task['text'], 'task', task.get('task_id')
                )
            decrypted_tasks.append(task_copy)
        
        logger.debug(f"Decrypted {len(decrypted_tasks)} tasks")
        return decrypted_tasks
    
    def decrypt_reminders(self, encrypted_reminders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Расшифровка напоминаний"""
        decrypted_reminders = []
        for reminder in encrypted_reminders:
            reminder_copy = reminder.copy()
            if 'text' in reminder_copy and reminder_copy['text']:
                reminder_copy['text'] = self._decrypt_safely(
                    reminder['text'], 'reminder', reminder.get('reminder_id')
                )
            decrypted_reminders.append(reminder_copy)
        
        logger.debug(f"Decrypted {len(decrypted_reminders)} reminders")
        return decrypted_reminders
    
    def decrypt_diary_entries(self, encrypted_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Расшифровка записей дневника"""
        decrypted_entries = []
        for entry in encrypted_entries:
            entry_copy = entry.copy()
            if 'content' in entry_copy and entry_copy['content']:
                entry_copy['content'] = self._decrypt_safely(
                    entry['content'], 'diary_entry', entry.get('entry_id')
                )
            decrypted_entries.append(entry_copy)
        
        logger.debug(f"Decrypted {len(decrypted_entries)} diary entries")
        return decrypted_entries
    
    def decrypt_backup(self, backup_data: Dict[str, Any]) -> Dict[str, Any]:
        """Полная расшифровка бэкапа"""
        try:
            decrypted_backup = backup_data.copy()
            
            # Расшифровываем все зашифрованные данные
            if 'data' in decrypted_backup:
                data = decrypted_backup['data']
                
                if 'tasks' in data and data['tasks']:
                    data['tasks'] = self.decrypt_tasks(data['tasks'])
                    logger.info(f"Decrypted {len(data['tasks'])} tasks")
                
                if 'reminders' in data and data['reminders']:
                    data['reminders'] = self.decrypt_reminders(data['reminders'])
                    logger.info(f"Decrypted {len(data['reminders'])} reminders")
                
                if 'diary_entries' in data and data['diary_entries']:
                    data['diary_entries'] = self.decrypt_diary_entries(data['diary_entries'])
                    logger.info(f"Decrypted {len(data['diary_entries'])} diary entries")
                
                # Пользователи и категории не зашифрованы, оставляем как есть
                if 'users' in data:
                    logger.info(f"Found {len(data['users'])} users (not encrypted)")
                
                if 'task_categories' in data:
                    logger.info(f"Found {len(data['task_categories'])} categories (not encrypted)")
            
            logger.info("Backup decryption completed successfully")
            return decrypted_backup
            
        except Exception as e:
            logger.error(f"Error decrypting backup: {e}")
            raise
    
    def save_decrypted_backup(self, decrypted_backup: Dict[str, Any], output_path: str):
        """Сохранение расшифрованного бэкапа в файл"""
        output_path = Path(output_path)
        
        try:
            def json_serializer(obj):
                """Кастомный сериализатор для JSON"""
                if isinstance(obj, datetime):
                    return obj.isoformat()
                elif hasattr(obj, 'date') and callable(getattr(obj, 'date')):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
            
            # Создаем директорию если не существует
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(decrypted_backup, f, ensure_ascii=False, indent=2, default=json_serializer)
            
            logger.info(f"Decrypted backup saved to {output_path}")
            
        except Exception as e:
            logger.error(f"Error saving decrypted backup: {e}")
            raise
    
    def print_backup_summary(self, backup_data: Dict[str, Any]):
        """Печать сводки по бэкапу"""
        metadata = backup_data.get('metadata', {})
        stats = metadata.get('database_statistics', {})
        
        print("\n" + "=" * 60)
        print(" " * 20 + "BACKUP SUMMARY")
        print("=" * 60)
        print(f"Version: {metadata.get('version', 'unknown')}")
        print(f"Created: {metadata.get('created_at', 'unknown')}")
        print(f"Bot: {metadata.get('bot_info', {}).get('username', 'unknown')}")
        print()
        print("Database Statistics:")
        print(f"  👥 Users: {stats.get('users_count', 0)}")
        print(f"  📋 Tasks: {stats.get('tasks_count', 0)} (Active: {stats.get('active_tasks', 0)})")
        print(f"  ⏰ Reminders: {stats.get('reminders_count', 0)} (Active: {stats.get('active_reminders', 0)})")
        print(f"  📝 Diary Entries: {stats.get('diary_entries_count', 0)}")
        print(f"  📂 Categories: {stats.get('task_categories_count', 0)}")
        print("=" * 60)
    
    def validate_backup_structure(self, backup_data: Dict[str, Any]) -> bool:
        """Проверка структуры бэкапа"""
        try:
            # Проверяем обязательные поля
            if 'metadata' not in backup_data:
                logger.warning("No metadata found in backup")
                return False
            
            if 'data' not in backup_data:
                logger.error("No data section found in backup")
                return False
            
            logger.info("Backup structure validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Backup validation failed: {e}")
            return False


def get_encryption_key_interactive() -> str:
    """Интерактивное получение ключа шифрования"""
    print("\n🔑 Введите ключ шифрования:")
    print("(Ключ должен быть тем же, что использовался при создании бэкапа)")
    
    # Попробуем найти ключ в переменных окружения
    env_key = os.getenv('ENCRYPTION_KEY')
    if env_key:
        use_env = input(f"Найден ключ в переменной окружения ENCRYPTION_KEY. Использовать? (y/n): ")
        if use_env.lower() in ['y', 'yes', 'да']:
            return env_key
    
    # Ручной ввод ключа
    key = input("Ключ шифрования: ").strip()
    if not key:
        print("❌ Ключ не может быть пустым!")
        return get_encryption_key_interactive()
    
    return key


def main():
    """Основная функция"""
    parser = argparse.ArgumentParser(
        description="Автономный декриптор бэкапов TG-PLANNER-BOT",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s backup.json.gz                          # Интерактивная расшифровка
  %(prog)s backup.json.gz -k YOUR_KEY              # С указанием ключа
  %(prog)s backup.json.gz -o decrypted.json        # С указанием выходного файла
  %(prog)s backup.json.gz -k YOUR_KEY --summary    # Только показать сводку
        """
    )
    
    parser.add_argument(
        'input_file',
        help='Путь к зашифрованному бэкапу (.json.gz)'
    )
    
    parser.add_argument(
        '-k', '--key',
        help='Ключ шифрования (можно указать через переменную окружения ENCRYPTION_KEY)'
    )
    
    parser.add_argument(
        '-o', '--output',
        help='Путь к выходному файлу (по умолчанию: decrypted_backup.json)'
    )
    
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Показать только сводку по бэкапу без расшифровки'
    )
    
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Только проверить структуру бэкапа'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Подробный вывод'
    )
    
    args = parser.parse_args()
    
    # Настройка уровня логирования
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Получение ключа шифрования
        if args.key:
            encryption_key = args.key
        elif os.getenv('ENCRYPTION_KEY'):
            encryption_key = os.getenv('ENCRYPTION_KEY')
            print(f"🔑 Используется ключ из переменной окружения ENCRYPTION_KEY")
        else:
            encryption_key = get_encryption_key_interactive()
        
        # Создание декриптора
        print(f"📂 Загрузка бэкапа из {args.input_file}...")
        decryptor = StandaloneBackupDecryptor(encryption_key)
        
        # Загрузка бэкапа
        backup_data = decryptor.load_backup_from_file(args.input_file)
        
        # Проверка структуры
        if not decryptor.validate_backup_structure(backup_data):
            print("❌ Неверная структура бэкапа!")
            sys.exit(1)
        
        # Показ сводки
        decryptor.print_backup_summary(backup_data)
        
        if args.validate:
            print("✅ Структура бэкапа корректна!")
            return
        
        if args.summary:
            print("ℹ️  Показана только сводка (используйте без --summary для расшифровки)")
            return
        
        # Расшифровка
        print("\n🔓 Расшифровка бэкапа...")
        decrypted_backup = decryptor.decrypt_backup(backup_data)
        
        # Сохранение
        output_file = args.output or 'decrypted_backup.json'
        print(f"💾 Сохранение расшифрованного бэкапа в {output_file}...")
        decryptor.save_decrypted_backup(decrypted_backup, output_file)
        
        print(f"\n✅ Расшифровка завершена успешно!")
        print(f"📄 Расшифрованный бэкап сохранен: {output_file}")
        
    except KeyboardInterrupt:
        print("\n❌ Операция прервана пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"\n❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#Python 3.7+
#cryptography==42.0.5

#how to use:

#base:
#python backup_decryptor.py backup.json.gz

#with key:
#python backup_decryptor.py backup.json.gz -k "your_key_here"

#with output file:
#python backup_decryptor.py backup.json.gz -o my_decrypted_backup.json

## 🔧 Параметры командной строки

# | Параметр | Описание |
# |----------|----------|
# | `input_file` | Путь к зашифрованному бэкапу (.json.gz) |
# | `-k, --key` | Ключ шифрования |
# | `-o, --output` | Путь к выходному файлу (по умолчанию: decrypted_backup.json) |
# | `--summary` | Показать только сводку по бэкапу |
# | `--validate` | Только проверить структуру бэкапа |
# | `-v, --verbose` | Подробный вывод |
# | `-h, --help` | Показать справку |

# export ENCRYPTION_KEY="your_key_here"
# python backup_decryptor.py backup.json.gz
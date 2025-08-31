import base64
import secrets
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class EncryptionService:
    def __init__(self, master_key: str):
        """
        Инициализация сервиса шифрования
        
        Args:
            master_key: Мастер-ключ из переменной окружения
        """
        if not master_key:
            raise ValueError("Master key is required for encryption")
        
        # Генерируем ключ для Fernet из мастер-ключа
        self.fernet = self._create_fernet_key(master_key)
        logger.info("Encryption service initialized")
    
    def _create_fernet_key(self, master_key: str) -> Fernet:
        """
        Создание ключа Fernet из мастер-ключа
        
        Args:
            master_key: Мастер-ключ в виде строки
            
        Returns:
            Объект Fernet для шифрования/расшифровки
        """
        # Используем фиксированную соль для постоянства ключа
        # В продакшене лучше хранить соль отдельно
        salt = b'salt_for_tg_planner_bot_2024'
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return Fernet(key)
    
    def encrypt(self, data: str) -> str:
        """
        Шифрование строки
        
        Args:
            data: Строка для шифрования
            
        Returns:
            Зашифрованная строка в base64
        """
        if not data:
            return ""
        
        try:
            encrypted_data = self.fernet.encrypt(data.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted_data).decode('utf-8')
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise
    
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
    
    def encrypt_if_not_none(self, data: Optional[str]) -> Optional[str]:
        """
        Шифрование с проверкой на None
        
        Args:
            data: Строка для шифрования или None
            
        Returns:
            Зашифрованная строка или None
        """
        return self.encrypt(data) if data is not None else None
    
    def decrypt_if_not_none(self, encrypted_data: Optional[str]) -> Optional[str]:
        """
        Расшифровка с проверкой на None
        
        Args:
            encrypted_data: Зашифрованная строка или None
            
        Returns:
            Расшифрованная строка или None
        """
        return self.decrypt(encrypted_data) if encrypted_data is not None else None

def generate_encryption_key() -> str:
    """
    Генерация случайного ключа шифрования
    
    Returns:
        Случайный ключ в виде строки
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')

# Глобальный экземпляр сервиса шифрования
_encryption_service: Optional[EncryptionService] = None

def get_encryption_service() -> EncryptionService:
    """
    Получение глобального экземпляра сервиса шифрования
    
    Returns:
        Экземпляр EncryptionService
    """
    global _encryption_service
    if _encryption_service is None:
        from config import ENCRYPTION_KEY
        if not ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY not found in environment variables")
        _encryption_service = EncryptionService(ENCRYPTION_KEY)
    return _encryption_service

def encrypt_text(text: str) -> str:
    """Быстрый доступ к шифрованию"""
    return get_encryption_service().encrypt(text)

def decrypt_text(encrypted_text: str) -> str:
    """Быстрый доступ к расшифровке"""
    return get_encryption_service().decrypt(encrypted_text)
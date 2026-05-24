"""
Сетевые вспомогательные функции для лаунчера и диалогов подключения.

Не выполняет игровой протокол — только определение локального IP
и проверка доступности TCP-порта перед запуском сервера.
"""

import socket


def get_local_ip() -> str:
    """
    Определяет IPv4-адрес хоста в локальной сети (LAN).

    Сначала выполняется «фиктивное» UDP-подключение к внешнему адресу,
    чтобы ОС выбрала интерфейс по умолчанию; при ошибке — резолв имени хоста.

    Returns:
        Строка с IP-адресом или ``127.0.0.1``, если определить адрес не удалось.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """
    Проверяет, свободен ли TCP-порт для привязки сервера.

    Args:
        port: Номер порта (ожидается диапазон 1024–65535).
        host: Адрес привязки; по умолчанию все интерфейсы.

    Returns:
        True, если ``bind`` завершился успешно (порт можно использовать).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False

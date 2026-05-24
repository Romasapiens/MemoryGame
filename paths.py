"""
Разрешение путей к ресурсам и пользовательским данным.

Модуль обеспечивает единообразный доступ к файлам при запуске из исходников
(``python main.py``) и из собранного исполняемого файла (PyInstaller).
Рабочая директория при старте приложения выставляется в :func:`ensure_runtime_paths`.
"""

from __future__ import annotations

import os
import sys

# Допустимые расширения файлов в каталоге card_images
_CARD_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')


def is_frozen() -> bool:
    """
    Проверяет, запущено ли приложение как собранный .exe (PyInstaller).

    Returns:
        True, если интерпретатор работает в режиме frozen (``sys.frozen``).
    """
    return getattr(sys, "frozen", False)


def app_dir() -> str:
    """
    Возвращает каталог приложения для чтения и записи пользовательских данных.

    Для .exe — папка с исполняемым файлом; для исходников — каталог проекта.

    Returns:
        Абсолютный путь к корню приложения.
    """
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """
    Возвращает каталог встроенных ресурсов (только чтение).

    В режиме PyInstaller это временная папка ``_MEIPASS`` с распакованными данными.

    Returns:
        Абсолютный путь к каталогу ресурсов.
    """
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def data_path(filename: str) -> str:
    """
    Формирует путь к изменяемому файлу рядом с приложением.

    Args:
        filename: Имя файла (например, ``scores.json``, ``theme_config.txt``).

    Returns:
        Полный путь ``app_dir() / filename``.
    """
    return os.path.join(app_dir(), filename)


def resource_path(*parts: str) -> str:
    """
    Формирует путь к ресурсу, встроенному в дистрибутив.

    Args:
        *parts: Компоненты пути относительно :func:`resource_dir`.

    Returns:
        Полный путь к ресурсу.
    """
    return os.path.join(resource_dir(), *parts)


def card_images_dir() -> str | None:
    """
    Ищет каталог ``card_images`` с изображениями пар карт.

    Порядок поиска зависит от режима запуска: в .exe сначала ``_MEIPASS``,
    затем папка рядом с exe; в исходниках — каталог проекта и текущая рабочая папка.

    Returns:
        Абсолютный путь к каталогу или ``None``, если каталог не найден.
    """
    candidates: list[str] = []
    if is_frozen():
        candidates.append(resource_path("card_images"))
        candidates.append(os.path.join(app_dir(), "card_images"))
    else:
        candidates.append(os.path.join(app_dir(), "card_images"))
        candidates.append(os.path.join(os.getcwd(), "card_images"))
        candidates.append(os.path.abspath("card_images"))

    seen: set[str] = set()
    for folder in candidates:
        folder = os.path.normpath(folder)
        if folder in seen:
            continue
        seen.add(folder)
        if os.path.isdir(folder):
            return folder
    return None


def ensure_runtime_paths() -> str:
    """
    Устанавливает рабочую директорию процесса в каталог приложения.

    Нужно для корректного поиска ``card_images`` при запуске ``main.py`` из
    произвольной папки (как при старом запуске ``client.py`` из каталога игры).

    Returns:
        Путь к каталогу, установленному как текущий рабочий.
    """
    base = app_dir()
    try:
        os.chdir(base)
    except OSError:
        pass
    return base


def list_card_image_paths() -> list[str]:
    """
    Возвращает отсортированный список путей к файлам изображений карт.

    Returns:
        Список абсолютных путей к файлам с расширениями из :data:`_CARD_EXTENSIONS`.
        Пустой список, если каталог не найден.
    """
    folder = card_images_dir()
    if not folder:
        return []
    files = [
        f for f in os.listdir(folder)
        if f.lower().endswith(_CARD_EXTENSIONS)
        and os.path.isfile(os.path.join(folder, f))
    ]
    files.sort()
    return [os.path.join(folder, f) for f in files]

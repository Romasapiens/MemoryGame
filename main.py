"""
Точка входа сетевой игры Memory Game.

Модуль реализует графический лаунчер (:class:`AppLauncher`): выбор между запуском
встроенного TCP-сервера и подключением к удалённому серверу. При создании сервера
:class:`~server.MemoryServer` работает в фоновом потоке, окно клиента
:class:`~client.MemoryGUI` открывается в том же процессе.

Запуск:
    python main.py

Сборка:
    build_exe.bat → dist/MemoryGame.exe
"""

from __future__ import annotations

import socket
import threading
import tkinter as tk
from tkinter import messagebox

from paths import ensure_runtime_paths

# Рабочая директория = каталог проекта / exe (для card_images и scores.json)
ensure_runtime_paths()

from client import MemoryGUI
from network_utils import get_local_ip, is_port_available
from server import MemoryServer


class AppLauncher:
    """
    Стартовое окно приложения: хост сервера или подключение клиента.

    Attributes:
        root: Корневое окно Tkinter лаунчера.
        server_thread: Поток с циклом accept сервера (daemon).
        server_instance: Экземпляр :class:`~server.MemoryServer` или ``None``.
        client_gui: Активное окно :class:`~client.MemoryGUI` или ``None``.
        host_port: Порт, на котором слушает локальный сервер.
        host_lan_ip: LAN-адрес хоста для показа другим игрокам.
    """

    DEFAULT_PORT = 5555

    def __init__(self) -> None:
        """Инициализирует лаунчер и отображает главное меню."""
        self.root = tk.Tk()
        self.root.title("Memory Game")
        self.root.geometry("460x380")
        self.root.resizable(False, False)

        self.server_thread: threading.Thread | None = None
        self.server_instance: MemoryServer | None = None
        self.client_gui: MemoryGUI | None = None
        self.host_port: int | None = None
        self.host_lan_ip: str | None = None

        self.colors = {
            'bg': '#f0f0f0', 'fg': '#000000',
            'btn_bg': '#e0e0e0', 'btn_fg': '#000000',
            'btn_active': '#d0d0d0', 'frame_bg': '#f0f0f0',
        }
        self.root.configure(bg=self.colors['bg'])
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self) -> None:
        """Создаёт элементы интерфейса лаунчера (кнопки режимов, IP, выход)."""
        frame = tk.Frame(self.root, bg=self.colors['frame_bg'])
        frame.pack(expand=True, fill=tk.BOTH, padx=24, pady=20)

        tk.Label(
            frame, text="Memory Game", font=("Arial", 22, "bold"),
            bg=self.colors['frame_bg'], fg=self.colors['fg'],
        ).pack(pady=(0, 8))

        tk.Label(
            frame,
            text="Сетевая игра «Мемори»\n"
                 "Один игрок создаёт сервер, остальные подключаются по IP.",
            font=("Arial", 10), justify=tk.CENTER,
            bg=self.colors['frame_bg'], fg=self.colors['fg'],
        ).pack(pady=(0, 16))

        tk.Button(
            frame, text="Создать сервер и играть",
            command=self.start_server_and_play,
            font=("Arial", 12), width=28,
            bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
            activebackground=self.colors['btn_active'],
        ).pack(pady=8)

        tk.Button(
            frame, text="Подключиться к серверу",
            command=self.join_server,
            font=("Arial", 12), width=28,
            bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
            activebackground=self.colors['btn_active'],
        ).pack(pady=8)

        tk.Label(
            frame,
            text=f"Ваш IP в сети: {get_local_ip()}",
            font=("Arial", 9), bg=self.colors['frame_bg'], fg='#555555',
        ).pack(pady=(12, 0))

        tk.Button(
            frame, text="Выход", command=self.root.destroy,
            font=("Arial", 10), width=14,
            bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
            activebackground=self.colors['btn_active'],
        ).pack(pady=(16, 0))

    def start_server_and_play(self) -> None:
        """
        Запускает локальный сервер и открывает клиент на 127.0.0.1.

        Проверяет доступность порта, создаёт :class:`~server.MemoryServer`,
        показывает диалог с IP для других игроков и через 400 мс открывает GUI клиента.
        """
        port = self.ask_port()
        if port is None:
            return
        if not is_port_available(port):
            messagebox.showerror(
                "Ошибка",
                f"Порт {port} уже занят.\nВыберите другой порт или закройте другое приложение.",
            )
            return

        try:
            self.server_instance = MemoryServer(host='0.0.0.0', port=port)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить сервер:\n{e}")
            return

        self.host_port = port
        self.host_lan_ip = get_local_ip()
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

        self.show_host_info_dialog()
        self.root.after(400, lambda: self.launch_client('127.0.0.1', port, is_host=True))

    def show_host_info_dialog(self) -> None:
        """Показывает IP и порт для подключения гостей в локальной сети."""
        ip = self.host_lan_ip
        port = self.host_port
        msg = (
            f"Сервер запущен!\n\n"
            f"Другие игроки в вашей сети (Wi‑Fi / LAN) подключаются так:\n"
            f"  IP:   {ip}\n"
            f"  Порт: {port}\n\n"
            f"На этом компьютере вы играете как хост (127.0.0.1).\n"
            f"Разрешите доступ в брандмауэре Windows, если появится запрос."
        )
        messagebox.showinfo("Сервер запущен", msg)

    def _run_server(self) -> None:
        """Точка входа фонового потока: цикл приёма подключений сервера."""
        try:
            if self.server_instance:
                self.server_instance.run()
        except Exception as e:
            print(f"Ошибка сервера: {e}")

    def launch_client(self, host: str, port: int, is_host: bool = False) -> None:
        """
        Скрывает лаунчер и запускает главный цикл окна клиента.

        Args:
            host: IP-адрес сервера (для хоста — 127.0.0.1).
            port: TCP-порт сервера.
            is_host: True, если этот экземпляр также поднял сервер (подсказка с LAN IP).
        """
        self.root.withdraw()
        if is_host and self.host_lan_ip and self.host_port:
            hint = (
                f"Сервер: {self.host_lan_ip}:{self.host_port} "
                f"(сообщите этот адрес другим игрокам)"
            )
        else:
            hint = f"Подключение к {host}:{port}"

        self.client_gui = MemoryGUI(
            host=host,
            port=port,
            connection_hint=hint,
            on_quit_callback=self.on_client_close,
        )
        self.client_gui.server_instance = self.server_instance
        self.client_gui.root.protocol("WM_DELETE_WINDOW", self._client_window_close)
        self.client_gui.run()

    def _client_window_close(self) -> None:
        """Обработчик закрытия окна клиента по крестику — с подтверждением."""
        if messagebox.askyesno("Выход", "Выйти из игры и вернуться в меню?"):
            self.on_client_close()

    def ask_port(self) -> int | None:
        """
        Модальный диалог ввода порта сервера.

        Returns:
            Выбранный порт (1024–65535) или ``None``, если пользователь отменил ввод.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Настройки сервера")
        dialog.geometry("280x170")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Порт сервера:", font=("Arial", 10)).pack(pady=(18, 4))
        port_var = tk.StringVar(value=str(self.DEFAULT_PORT))
        port_entry = tk.Entry(dialog, textvariable=port_var, font=("Arial", 11), width=12)
        port_entry.pack(pady=4)
        port_entry.focus_set()
        tk.Label(
            dialog, text="(1024–65535, по умолчанию 5555)",
            font=("Arial", 8), fg='#666666',
        ).pack()

        result: list[int] = []

        def ok() -> None:
            try:
                p = int(port_var.get())
                if 1024 <= p <= 65535:
                    result.append(p)
                    dialog.destroy()
                else:
                    messagebox.showerror("Ошибка", "Порт должен быть от 1024 до 65535")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите целое число")

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="Запустить", command=ok, width=10).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="Отмена", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=6)

        self.root.wait_window(dialog)
        return result[0] if result else None

    def join_server(self) -> None:
        """Диалог ввода IP и порта удалённого сервера с последующим запуском клиента."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Подключение к серверу")
        dialog.geometry("320x220")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Введите IP компьютера, где запущен сервер:",
            font=("Arial", 9), wraplength=280,
        ).pack(pady=(14, 4))

        ip_var = tk.StringVar(value=get_local_ip())
        ip_entry = tk.Entry(dialog, textvariable=ip_var, font=("Arial", 11), width=22)
        ip_entry.pack(pady=4)
        ip_entry.focus_set()

        tk.Label(dialog, text="Порт:", font=("Arial", 10)).pack(pady=(6, 0))
        port_var = tk.StringVar(value=str(self.DEFAULT_PORT))
        port_entry = tk.Entry(dialog, textvariable=port_var, font=("Arial", 11), width=10)
        port_entry.pack(pady=4)

        result: dict[str, str | int] = {}

        def ok() -> None:
            ip = ip_var.get().strip()
            try:
                port = int(port_var.get())
                if not ip or port < 1024 or port > 65535:
                    raise ValueError
                socket.inet_aton(ip)
                result['host'] = ip
                result['port'] = port
                dialog.destroy()
            except (ValueError, OSError):
                messagebox.showerror("Ошибка", "Некорректные IP или порт")

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="Подключиться", command=ok, width=14).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Отмена", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)
        if 'host' in result:
            self.launch_client(str(result['host']), int(result['port']), is_host=False)

    def on_client_close(self) -> None:
        """
        Закрывает клиент, останавливает локальный сервер (если был) и показывает лаунчер.

        Вызывается из :meth:`launch_client` через callback клиента.
        """
        if self.client_gui:
            self.client_gui.mark_closing()
            try:
                self.client_gui.root.destroy()
            except tk.TclError:
                pass
            self.client_gui = None
        if self.server_instance:
            self.server_instance.shutdown()
            self.server_instance = None
        self.host_port = None
        self.host_lan_ip = None
        self.root.deiconify()

    def on_close(self) -> None:
        """Полное завершение приложения: сервер, клиент и окно лаунчера."""
        if self.server_instance:
            self.server_instance.shutdown()
            self.server_instance = None
        if self.client_gui:
            self.client_gui.mark_closing()
            try:
                self.client_gui.root.destroy()
            except tk.TclError:
                pass
            self.client_gui = None
        self.root.destroy()

    def run(self) -> None:
        """Запускает главный цикл обработки событий Tkinter лаунчера."""
        self.root.mainloop()


if __name__ == "__main__":
    AppLauncher().run()

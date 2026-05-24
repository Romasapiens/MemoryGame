"""
Клиентская часть сетевой игры Memory (GUI + сеть).

Класс :class:`MemoryGUI` реализует экраны входа, меню, игры и чата,
фоновый приём сообщений в :meth:`MemoryGUI.receive_data` и отображение
состояния поля из ``game_state``, присланного сервером.

Запуск отдельно: ``python client.py`` (после :func:`paths.ensure_runtime_paths`).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
import socket
import pickle
import threading
import io
import os
from PIL import Image, ImageTk
from paths import data_path, ensure_runtime_paths


def send_msg(sock: socket.socket, data: object) -> None:
    """Отправляет сообщение серверу (4 байта длины + pickle)."""
    msg = pickle.dumps(data)
    length = len(msg)
    sock.sendall(length.to_bytes(4, 'big') + msg)


def recv_msg(sock: socket.socket) -> object:
    """
    Принимает сообщение от сервера.

    Raises:
        ConnectionError: При обрыве соединения.
    """
    header = b''
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("Соединение закрыто")
        header += chunk
    length = int.from_bytes(header, 'big')
    data = b''
    while len(data) < length:
        chunk = sock.recv(min(length - len(data), 4096))
        if not chunk:
            raise ConnectionError("Соединение закрыто до получения всех данных")
        data += chunk
    return pickle.loads(data)


# Палитры цветов интерфейса для светлой и тёмной темы
THEMES = {
    'light': {
        'bg': '#f0f0f0', 'fg': '#000000', 'btn_bg': '#e0e0e0', 'btn_fg': '#000000',
        'btn_active': '#d0d0d0', 'frame_bg': '#f0f0f0', 'entry_bg': '#ffffff',
        'entry_fg': '#000000', 'label_bg': '#f0f0f0', 'top_bg': '#add8e6',
        'game_bg': '#c0c0c0', 'chat_bg': '#ffffff', 'chat_fg': '#000000',
        'highlight': '#ffff99', 'win_bg': '#90ee90', 'tie_bg': '#ffa500',
    },
    'dark': {
        'bg': '#2e2e2e', 'fg': '#ffffff', 'btn_bg': '#555555', 'btn_fg': '#ffffff',
        'btn_active': '#777777', 'frame_bg': '#2e2e2e', 'entry_bg': '#444444',
        'entry_fg': '#ffffff', 'label_bg': '#2e2e2e', 'top_bg': '#1e3a5f',
        'game_bg': '#3a3a3a', 'chat_bg': '#3a3a3a', 'chat_fg': '#ffffff',
        'highlight': '#ffd700', 'win_bg': '#2e7d32', 'tie_bg': '#e65100',
    }
}

# Персистентная тема и подписи для OptionMenu
THEME_FILE = data_path("theme_config.txt")
THEME_LABELS = {'light': 'Светлая', 'dark': 'Тёмная'}
THEME_BY_LABEL = {v: k for k, v in THEME_LABELS.items()}


class MemoryGUI:
    """
    Графический клиент сетевой игры Memory.

    Управляет Tkinter-интерфейсом, TCP-соединением с сервером и отображением
    ``game_state``. Не создаёт сервер — только подключается к host:port.
    """

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 5555,
        connection_hint: str | None = None,
        on_quit_callback=None,
    ) -> None:
        """
        Создаёт окно клиента и экран входа.

        Args:
            host: IP-адрес сервера.
            port: TCP-порт.
            connection_hint: Подсказка на экране входа.
            on_quit_callback: Callback при выходе (лаунчер).
        """
        self.root = tk.Tk()
        self.root.title("Memory – Сетевая игра")
        self.root.geometry("800x700")
        self.root.minsize(650, 500)

        self.current_theme = self._load_theme()
        self.colors = THEMES[self.current_theme]
        self.current_screen = 'login'

        self.server_host = host
        self.server_port = port
        self.connection_hint = connection_hint
        self.on_quit_callback = on_quit_callback
        self.server_instance = None

        self.client = None
        self.player_id = None
        self.nickname = None
        self.game_state = None
        self.nicknames_all = []

        self.pending_settings = None
        self.pending_connect_settings = None

        self.connecting_dialog = None
        self.waiting_dialog = None
        self.connecting = False
        self.first_welcome = True
        self.session_active = False
        self._handling_login_error = False
        self._shutting_down = False

        self.card_buttons = []
        self.score_labels = []
        self.current_player_label = None
        self.card_photos = []
        self._tk_images: list[ImageTk.PhotoImage] = []

        self.turn_time = 0
        self.turn_timer_id = None
        self.turn_remaining = 0
        self.timer_label = None

        self.top_scores = []

        self._setup_ttk_style()
        self.setup_login_screen()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _load_theme(self) -> str:

        """Загружает тему из theme_config.txt."""
        if os.path.exists(THEME_FILE):
            with open(THEME_FILE, 'r') as f:
                theme = f.read().strip()
                if theme in THEMES:
                    return theme
        return 'light'

    def _save_theme(self, theme: str) -> None:

        """Сохраняет выбранную тему на диск."""
        with open(THEME_FILE, 'w') as f:
            f.write(theme)

    def _setup_ttk_style(self) -> None:

        """Настраивает тему ttk (clam) для Combobox."""
        self.ttk_style = ttk.Style(self.root)
        try:
            self.ttk_style.theme_use('clam')
        except tk.TclError:
            pass
        self._apply_ttk_style()

    def _apply_ttk_style(self) -> None:

        """Применяет цвета текущей темы к виджетам ttk."""
        c = self.colors
        self.ttk_style.configure(
            'TCombobox',
            fieldbackground=c['entry_bg'],
            background=c['btn_bg'],
            foreground=c['entry_fg'],
        )

    def apply_theme_to_widgets(self, parent=None) -> None:

        """Рекурсивно перекрашивает виджеты начиная с parent."""
        if parent is None:
            parent = self.root
        self.colors = THEMES[self.current_theme]
        if isinstance(parent, (tk.Tk, tk.Toplevel)):
            parent.configure(bg=self.colors['bg'])
        for child in parent.winfo_children():
            self._apply_theme_recursive(child)

    def _apply_theme_recursive(self, widget: tk.Widget) -> None:

        """Обход дерева виджетов для смены темы."""
        if isinstance(widget, tk.Frame):
            if not getattr(widget, 'custom_bg', False):
                widget.configure(bg=self.colors['frame_bg'])
        elif isinstance(widget, tk.Label):
            if not getattr(widget, 'custom_bg', False):
                widget.configure(bg=self.colors['label_bg'], fg=self.colors['fg'])
        elif isinstance(widget, tk.Button):
            if not getattr(widget, 'custom_bg', False):
                widget.configure(bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                                 activebackground=self.colors['btn_active'])
        elif isinstance(widget, tk.Entry):
            widget.configure(bg=self.colors['entry_bg'], fg=self.colors['entry_fg'],
                             insertbackground=self.colors['entry_fg'])
        elif isinstance(widget, tk.Text):
            widget.configure(bg=self.colors['chat_bg'], fg=self.colors['chat_fg'],
                             insertbackground=self.colors['chat_fg'])
        for child in widget.winfo_children():
            self._apply_theme_recursive(child)

    def clear_window(self) -> None:

        """Удаляет все дочерние виджеты корневого окна."""
        for widget in self.root.winfo_children():
            widget.destroy()

    def show_connecting_dialog(self, title: str, text: str) -> None:

        """Показывает модальное окно ожидания подключения."""
        if self.connecting_dialog:
            return
        self.connecting = True
        self.connecting_dialog = tk.Toplevel(self.root)
        self.connecting_dialog.title(title)
        self.connecting_dialog.geometry("300x100")
        self.connecting_dialog.resizable(False, False)
        self.connecting_dialog.transient(self.root)
        self.connecting_dialog.grab_set()
        self.apply_theme_to_widgets(self.connecting_dialog)
        tk.Label(self.connecting_dialog, text=text, font=("Arial", 12)).pack(pady=10)
        tk.Button(self.connecting_dialog, text="Отмена", command=self.cancel_connecting).pack(pady=5)

    def close_connecting_dialog(self) -> None:

        """Закрывает диалог подключения."""
        if self.connecting_dialog:
            self.connecting_dialog.destroy()
            self.connecting_dialog = None
        self.connecting = False

    def cancel_connecting(self) -> None:

        """Отмена подключения и возврат на ввод ника."""
        if self.client:
            try:
                self.client.close()
            except:
                pass
            self.client = None
        self.pending_connect_settings = None
        self.close_connecting_dialog()
        self._reset_login_form()

    def setup_login_screen(self) -> None:

        """Экран ввода никнейма."""
        self.clear_window()
        self.current_screen = 'login'
        self.root.configure(bg=self.colors['bg'])
        frame = tk.Frame(self.root, bg=self.colors['frame_bg'])
        frame.pack(expand=True)

        tk.Label(frame, text="Memory Game", font=("Arial", 24),
                 bg=self.colors['label_bg'], fg=self.colors['fg']).pack(pady=20)
        if self.connection_hint:
            tk.Label(
                frame, text=self.connection_hint, font=("Arial", 10),
                bg=self.colors['label_bg'], fg=self.colors['fg'], wraplength=420,
            ).pack(pady=(0, 10))
        tk.Label(frame, text="Ваш никнейм:", font=("Arial", 12),
                 bg=self.colors['label_bg'], fg=self.colors['fg']).pack(pady=5)
        self.nickname_entry = tk.Entry(frame, font=("Arial", 12),
                                       bg=self.colors['entry_bg'], fg=self.colors['entry_fg'],
                                       insertbackground=self.colors['entry_fg'])
        self.nickname_entry.pack(pady=5)
        if self.nickname:
            self.nickname_entry.insert(0, self.nickname)
        self.nickname_entry.focus_set()
        self.login_button = tk.Button(frame, text="Подключиться", command=self.check_nickname,
                                      font=("Arial", 12),
                                      bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                                      activebackground=self.colors['btn_active'])
        self.login_button.pack(pady=20)
        self.nickname_entry.bind('<Return>', lambda event: self.check_nickname())

    def check_nickname(self) -> None:

        """Запускает подключение к серверу в фоновом потоке."""
        self.nickname = self.nickname_entry.get().strip()
        if not self.nickname:
            messagebox.showerror("Ошибка", "Введите никнейм!")
            return
        self.session_active = False
        self._handling_login_error = False
        self.login_button.config(state=tk.DISABLED)
        self.nickname_entry.config(state=tk.DISABLED)
        self.show_connecting_dialog("Подключение", "Проверка ника...")
        self.first_welcome = True
        threading.Thread(target=self._check_nickname_thread, daemon=True).start()

    def _reset_login_form(self) -> None:

        """Восстанавливает форму входа после ошибки."""
        self.session_active = False
        if self.current_screen != 'login':
            self.setup_login_screen()
        elif hasattr(self, 'login_button'):
            self.login_button.config(state=tk.NORMAL)
            self.nickname_entry.config(state=tk.NORMAL)

    def _check_nickname_thread(self) -> None:

        """TCP-connect и поток receive_data."""
        if self.client:
            try:
                self.client.close()
            except:
                pass
            self.client = None
        try:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client.settimeout(5)
            self.client.connect((self.server_host, self.server_port))
            self.client.settimeout(None)
            threading.Thread(target=self.receive_data, daemon=True).start()
        except Exception as e:
            print(f"Ошибка подключения: {e}")
            self.client = None
            self.root.after(0, self._connection_failed)

    def _connection_failed(self) -> None:

        """Ошибка подключения на этапе входа."""
        self.close_connecting_dialog()
        self._reset_login_form()
        messagebox.showerror("Ошибка", "Не удалось подключиться к серверу")

    def _handle_nickname_rejected(self, error_text: str) -> None:

        """Отклонённый никнейм: диалог и экран login."""
        self._handling_login_error = True
        self.session_active = False
        self.first_welcome = True
        if self.client:
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None
        self.close_connecting_dialog()
        messagebox.showerror("Ошибка", error_text)
        self._reset_login_form()

    def show_main_menu(self) -> None:

        """Главное меню: режимы игры и топ."""
        if not self.session_active:
            self.setup_login_screen()
            return
        self.clear_window()
        self.cancel_timer()
        self.current_screen = 'menu'
        self.root.configure(bg=self.colors['bg'])
        frame = tk.Frame(self.root, bg=self.colors['frame_bg'])
        frame.pack(expand=True, fill=tk.BOTH)

        top_bar = tk.Frame(frame, bg=self.colors['frame_bg'])
        top_bar.pack(fill=tk.X, pady=(10,0))
        tk.Label(top_bar, text="", bg=self.colors['frame_bg']).pack(side=tk.LEFT, expand=True)
        tk.Label(top_bar, text="Тема:", font=("Arial", 10), bg=self.colors['frame_bg'],
                 fg=self.colors['fg']).pack(side=tk.LEFT, padx=(0, 5))
        theme_display = tk.StringVar(value=THEME_LABELS[self.current_theme])

        def on_theme_pick(label: str) -> None:
            theme_key = THEME_BY_LABEL.get(label)
            if theme_key:
                self.change_theme(theme_key)

        theme_menu = tk.OptionMenu(
            top_bar, theme_display, *THEME_LABELS.values(), command=on_theme_pick,
        )
        theme_menu.config(
            bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
            activebackground=self.colors['btn_active'], highlightthickness=0,
        )
        theme_menu["menu"].config(bg=self.colors['btn_bg'], fg=self.colors['btn_fg'])
        theme_menu.pack(side=tk.LEFT, padx=(0, 10))

        inner = tk.Frame(frame, bg=self.colors['frame_bg'])
        inner.pack(expand=True)
        tk.Label(inner, text=f"Добро пожаловать, {self.nickname}!",
                 font=("Arial", 16), bg=self.colors['label_bg'],
                 fg=self.colors['fg']).pack(pady=20)

        if self.top_scores:
            tk.Label(inner, text="Топ игроков:", font=("Arial", 12, "bold"),
                     bg=self.colors['label_bg'], fg=self.colors['fg']).pack(pady=(10, 0))
            for i, (name, pts) in enumerate(self.top_scores, 1):
                tk.Label(inner, text=f"{i}. {name} — {pts} очк.",
                         bg=self.colors['label_bg'], fg=self.colors['fg']).pack()

        btn_quick = tk.Button(inner, text="Быстрая игра", command=self.quick_game,
                              font=("Arial", 14), width=25,
                              bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                              activebackground=self.colors['btn_active'])
        btn_quick.pack(pady=10)

        btn_bot = tk.Button(inner, text="Игра с ботом", command=self.bot_game,
                            font=("Arial", 14), width=25,
                            bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                            activebackground=self.colors['btn_active'])
        btn_bot.pack(pady=10)

        btn_custom = tk.Button(inner, text="Своя игра", command=self.custom_game,
                               font=("Arial", 14), width=25,
                               bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                               activebackground=self.colors['btn_active'])
        btn_custom.pack(pady=10)

        btn_exit = tk.Button(inner, text="Выход", command=self.confirm_exit,
                             font=("Arial", 14), width=25,
                             bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                             activebackground=self.colors['btn_active'])
        btn_exit.pack(pady=10)

    def confirm_exit(self) -> None:

        """Подтверждение выхода из приложения."""
        if messagebox.askyesno("Выход", "Вы уверены, что хотите выйти из игры?"):
            self._finish_quit()

    def mark_closing(self) -> None:
        """Пользователь закрывает приложение — не показывать «соединение потеряно»."""
        self._shutting_down = True
        self.cancel_timer()
        if self.client:
            try:
                send_msg(self.client, {'type': 'leave_queue'})
            except OSError:
                pass
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None

    def _finish_quit(self) -> None:

        """Закрытие сокета и callback лаунчера."""
        self.mark_closing()
        if self.on_quit_callback:
            self.on_quit_callback()
        else:
            self.root.destroy()

    def change_theme(self, new_theme: str) -> None:

        """Смена светлой/тёмной темы."""
        if new_theme not in THEMES:
            return
        if new_theme == self.current_theme:
            return
        self.current_theme = new_theme
        self.colors = THEMES[self.current_theme]
        self._save_theme(new_theme)
        self._apply_ttk_style()
        self.root.after(10, self._refresh_current_screen)

    def _refresh_current_screen(self) -> None:

        """Пересоздание текущего экрана после смены темы."""
        if self.current_screen == 'login':
            self.setup_login_screen()
        elif self.current_screen == 'menu':
            self.show_main_menu()
        elif self.current_screen == 'game':
            self.setup_game_screen()

    def start_turn_timer(self, seconds: int) -> None:

        """Обратный отсчёт хода."""
        self.cancel_timer()
        if seconds <= 0:
            return
        self.turn_remaining = seconds
        self._update_timer_label()
        self._tick_timer()

    def _tick_timer(self) -> None:

        """Шаг таймера; при нуле — timeout на сервер."""
        if self.turn_remaining <= 0:
            try:
                send_msg(self.client, {'type': 'timeout'})
            except:
                pass
            return
        self.turn_remaining -= 1
        self._update_timer_label()
        self.turn_timer_id = self.root.after(1000, self._tick_timer)

    def _update_timer_label(self) -> None:

        """Обновление метки таймера."""
        if self.timer_label:
            mins, secs = divmod(self.turn_remaining, 60)
            self.timer_label.config(text=f"⏳ {mins:02d}:{secs:02d}")

    def cancel_timer(self) -> None:

        """Остановка таймера хода."""
        if self.turn_timer_id is not None:
            self.root.after_cancel(self.turn_timer_id)
            self.turn_timer_id = None
        self.turn_remaining = 0

    def ensure_connection_and_start(self, settings: dict) -> None:

        """Постановка в очередь game_settings."""
        if self.client is not None:
            self.send_settings_and_wait(settings)
        else:
            self.show_connecting_dialog("Подключение", "Подключение к серверу...")
            self.pending_connect_settings = settings
            threading.Thread(target=self._reconnect_and_start, daemon=True).start()

    def _reconnect_and_start(self) -> None:

        """Переподключение перед отправкой настроек."""
        if self.client:
            try:
                self.client.close()
            except:
                pass
            self.client = None
        try:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client.settimeout(5)
            self.client.connect((self.server_host, self.server_port))
            self.client.settimeout(None)
            threading.Thread(target=self.receive_data, daemon=True).start()
        except Exception as e:
            self.pending_connect_settings = None
            self.root.after(0, self._connection_failed)

    def quick_game(self) -> None:

        """Быстрая игра 4x4, 2 игрока."""
        self.ensure_connection_and_start({
            'rows': 4, 'cols': 4, 'max_players': 2, 'quick': True, 'bot': False
        })

    def bot_game(self) -> None:

        """Игра против бота."""
        self.ensure_connection_and_start({
            'rows': 4, 'cols': 4, 'max_players': 2, 'quick': False, 'bot': True, 'turn_time': 0
        })

    def custom_game(self) -> None:

        """Диалог своей игры."""
        win = tk.Toplevel(self.root)
        win.title("Настройки игры")
        win.geometry("320x280")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        self.apply_theme_to_widgets(win)

        tk.Label(win, text="Размер поля:", font=("Arial", 12)).grid(
            row=0, column=0, columnspan=2, pady=(12, 4), sticky="w", padx=16,
        )
        size_var = tk.StringVar(value="4x4")
        sizes = ["2x2", "4x4", "6x6", "8x8"]
        size_frame = tk.Frame(win)
        size_frame.grid(row=1, column=0, columnspan=2, padx=16, sticky="w")
        tk.OptionMenu(size_frame, size_var, *sizes).pack(side=tk.LEFT)

        tk.Label(win, text="Количество игроков:", font=("Arial", 12)).grid(
            row=2, column=0, columnspan=2, pady=(10, 4), sticky="w", padx=16,
        )
        players_var = tk.StringVar(value="2")
        players_frame = tk.Frame(win)
        players_frame.grid(row=3, column=0, columnspan=2, padx=16, sticky="w")
        players_menu_widget = {'menu': None}

        def rebuild_players_menu(options: list[str]) -> None:
            if players_menu_widget['menu'] is not None:
                players_menu_widget['menu'].destroy()
            if players_var.get() not in options:
                players_var.set(options[0])
            menu = tk.OptionMenu(players_frame, players_var, *options)
            menu.pack(side=tk.LEFT)
            players_menu_widget['menu'] = menu

        def on_size_change(*_args) -> None:
            size = size_var.get()
            if size == "2x2":
                rebuild_players_menu(["2"])
            elif size == "4x4":
                rebuild_players_menu(["2", "3"])
            else:
                rebuild_players_menu(["2", "3", "4"])

        size_var.trace_add('write', on_size_change)
        on_size_change()

        tk.Label(win, text="Таймер на ход (сек):", font=("Arial", 12)).grid(
            row=4, column=0, columnspan=2, pady=(10, 4), sticky="w", padx=16,
        )
        timer_var = tk.StringVar(value="0")
        timer_frame = tk.Frame(win)
        timer_frame.grid(row=5, column=0, columnspan=2, padx=16, sticky="w")
        tk.OptionMenu(timer_frame, timer_var, "0", "30", "60", "90").pack(side=tk.LEFT)

        def submit() -> None:
            size_str = size_var.get().strip()
            if 'x' not in size_str:
                messagebox.showerror("Ошибка", "Выберите размер поля", parent=win)
                return
            try:
                rows, cols = map(int, size_str.lower().split('x'))
                players = int(players_var.get())
                turn_time = int(timer_var.get())
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректные настройки игры", parent=win)
                return
            cards = rows * cols
            if cards % 2 != 0:
                messagebox.showerror("Ошибка", "Число карт должно быть чётным", parent=win)
                return
            if players < 2 or players > 4:
                messagebox.showerror("Ошибка", "Игроков должно быть от 2 до 4", parent=win)
                return
            win.destroy()
            self.ensure_connection_and_start({
                'rows': rows, 'cols': cols, 'max_players': players,
                'turn_time': turn_time, 'quick': False, 'bot': False,
            })

        btn_frame = tk.Frame(win)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=16)
        tk.Button(btn_frame, text="Начать", command=submit, font=("Arial", 12), width=12).pack(
            side=tk.LEFT, padx=6,
        )
        tk.Button(btn_frame, text="Отмена", command=win.destroy, font=("Arial", 12), width=12).pack(
            side=tk.LEFT, padx=6,
        )

    def send_settings_and_wait(self, settings: dict) -> None:

        """game_settings и диалог ожидания."""
        self.pending_settings = settings
        self.waiting_dialog = tk.Toplevel(self.root)
        self.waiting_dialog.title("Ожидание")
        self.waiting_dialog.geometry("300x100")
        self.waiting_dialog.resizable(False, False)
        self.waiting_dialog.transient(self.root)
        self.waiting_dialog.grab_set()
        self.apply_theme_to_widgets(self.waiting_dialog)
        tk.Label(self.waiting_dialog, text="Поиск игры...", font=("Arial", 12)).pack(pady=10)
        tk.Button(self.waiting_dialog, text="Отмена", command=self.cancel_waiting).pack(pady=5)
        try:
            send_msg(self.client, {'type': 'game_settings', 'data': settings})
        except:
            messagebox.showerror("Ошибка", "Не удалось отправить настройки")
            self.cancel_waiting()

    def cancel_waiting(self) -> None:

        """Отмена ожидания в лобби."""
        try:
            send_msg(self.client, {'type': 'leave_queue'})
        except:
            pass
        if self.waiting_dialog:
            self.waiting_dialog.destroy()
            self.waiting_dialog = None
        self.pending_settings = None
        self.show_main_menu()

    def _safe_log(self, message: dict) -> None:
        """Выводит сообщение в консоль, опуская бинарные данные картинок."""
        if 'game_state' in message:
            state = message['game_state']
            if 'card_images' in state:
                log_msg = {**message, 'game_state': {**state, 'card_images': '<images omitted>'}}
                print("Получено:", log_msg)
                return
        if 'data' in message and isinstance(message['data'], dict) and 'card_images' in message['data']:
            log_msg = {**message, 'data': {**message['data'], 'card_images': '<images omitted>'}}
            print("Получено:", log_msg)
            return
        print("Получено:", message)

    def receive_data(self) -> None:

        """Фоновый приём сообщений (GUI через root.after)."""
        while True:
            try:
                message = recv_msg(self.client)
                self._safe_log(message)
                msg_type = message.get('type')

                if msg_type == 'request_nickname':
                    send_msg(self.client, {'type': 'nickname', 'nickname': self.nickname})

                elif msg_type == 'welcome':
                    print(message['message'])
                    self.session_active = True
                    self.player_id = message.get('client_id')
                    self.root.after(0, self.close_connecting_dialog)
                    if self.pending_connect_settings is not None:
                        settings = self.pending_connect_settings
                        self.pending_connect_settings = None
                        self.root.after(0, lambda s=settings: self.send_settings_and_wait(s))
                    elif self.first_welcome:
                        self.first_welcome = False
                        self.root.after(0, self.show_main_menu)

                elif msg_type == 'scores_update':
                    self.top_scores = message['data']
                    if self.current_screen == 'menu':
                        self.root.after(0, self.show_main_menu)

                elif msg_type == 'queue_joined':
                    if self.waiting_dialog:
                        for child in self.waiting_dialog.winfo_children():
                            if isinstance(child, tk.Label):
                                child.config(text=message.get('message', ''))
                    self.root.after(0, self.close_connecting_dialog)

                elif msg_type == 'queue_update':
                    if self.waiting_dialog:
                        for child in self.waiting_dialog.winfo_children():
                            if isinstance(child, tk.Label):
                                child.config(text=message.get('message', ''))

                elif msg_type == 'game_start':
                    self.player_id = message['player_id']
                    self.game_state = message['game_state']
                    self.nicknames_all = message.get('nicknames', [])
                    self.turn_time = message.get('turn_time', 0)
                    self.root.after(0, self.close_waiting_dialog)
                    self.root.after(0, self.setup_game_screen)
                    if self.turn_time > 0 and self.game_state['current_player'] == self.player_id:
                        self.root.after(100, lambda: self.start_turn_timer(self.turn_time))

                elif msg_type == 'game_state':
                    self.game_state = message['data']
                    self.root.after(0, self.update_board)
                    if 'message' in message:
                        print(f"📢 {message['message']}")
                    if self.game_state['current_player'] == self.player_id and not self.game_state.get('game_over'):
                        if self.turn_time > 0:
                            self.root.after(100, lambda: self.start_turn_timer(self.turn_time))
                    else:
                        self.cancel_timer()

                elif msg_type == 'game_aborted':
                    self.cancel_timer()
                    self.root.after(0, lambda msg=message: messagebox.showinfo("Игра окончена", msg.get('message', '')))
                    self.root.after(0, self.return_to_menu)

                elif msg_type == 'chat':
                    self.root.after(0, self.add_chat_message,
                                    message.get('nickname', 'Unknown'),
                                    message['data'])

                elif msg_type == 'system':
                    self.root.after(0, self.add_chat_message, 'Система', message['data'])

                elif msg_type == 'error':
                    err = str(message.get('data', ''))
                    if 'никнейм' in err.lower():
                        self.root.after(0, lambda e=err: self._handle_nickname_rejected(e))
                    else:
                        self.root.after(0, lambda e=err: messagebox.showerror("Ошибка", e))

                else:
                    print(f"Неизвестный тип сообщения: {msg_type}")

            except (pickle.PickleError, EOFError, ConnectionError) as e:
                if not self._shutting_down:
                    print(f"Соединение потеряно: {e}")
                if self._shutting_down or self._handling_login_error:
                    break
                if self.session_active:
                    self.root.after(0, self.on_disconnect)
                else:
                    self.root.after(0, self._connection_failed)
                break
            except Exception as e:
                print(f"Ошибка приёма: {e}")
                break

    def close_waiting_dialog(self) -> None:

        """Закрывает диалог ожидания."""
        if self.waiting_dialog:
            self.waiting_dialog.destroy()
            self.waiting_dialog = None

    def setup_game_screen(self) -> None:

        """Построение игрового экрана."""
        self.clear_window()
        self.cancel_timer()
        self.current_screen = 'game'
        self.root.configure(bg=self.colors['bg'])
        state = self.game_state
        rows, cols = state['rows'], state['cols']
        num_players = len(self.nicknames_all)

        if rows <= 2:
            self.root.geometry("800x650")
        elif rows <= 4:
            self.root.geometry("800x750")
        elif rows <= 6:
            self.root.geometry("900x800")
        else:
            self.root.geometry("1000x900")

        top_frame = tk.Frame(self.root, bg=self.colors['top_bg'])
        top_frame.custom_bg = True
        top_frame.pack(fill=tk.X, padx=10, pady=10)

        self.score_labels = []
        for i in range(num_players):
            top_frame.columnconfigure(i, weight=1)
            player_frame = tk.Frame(top_frame, bg=self.colors['top_bg'])
            player_frame.grid(row=0, column=i, padx=5, pady=5, sticky="nsew")
            name = self.nicknames_all[i] if i < len(self.nicknames_all) else f"Игрок {i+1}"
            tk.Label(player_frame, text=name, font=("Arial", 10, "bold"),
                     bg=self.colors['top_bg'], fg=self.colors['fg']).pack()
            score_label = tk.Label(player_frame, text="0", font=("Arial", 12, "bold"),
                                   bg=self.colors['top_bg'], fg=self.colors['fg'])
            score_label.pack()
            self.score_labels.append(score_label)

        info_frame = tk.Frame(top_frame, bg=self.colors['top_bg'])
        info_frame.grid(row=0, column=num_players, padx=20, sticky="e")
        self.current_player_label = tk.Label(info_frame, text="Ожидание хода...",
                                             font=("Arial", 13), bg=self.colors['highlight'])
        self.current_player_label.pack()
        self.timer_label = tk.Label(info_frame, text="", font=("Arial", 11),
                                    bg=self.colors['top_bg'], fg=self.colors['fg'])
        self.timer_label.pack()
        if self.turn_time > 0 and state['current_player'] == self.player_id:
            self.start_turn_timer(self.turn_time)

        game_frame = tk.Frame(self.root, bg=self.colors['game_bg'])
        game_frame.custom_bg = True
        game_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=(20,5))

        if rows <= 2:
            btn_w, btn_h, font_size = 8, 4, 24
        elif rows <= 4:
            btn_w, btn_h, font_size = 6, 3, 20
        elif rows <= 6:
            btn_w, btn_h, font_size = 5, 2, 16
        else:
            btn_w, btn_h, font_size = 4, 2, 12

        self.card_buttons = []
        for r in range(rows):
            row_buttons = []
            for c in range(cols):
                btn = tk.Button(
                    game_frame, text="❓", font=("Arial", font_size),
                    width=btn_w, height=btn_h,
                    command=lambda row=r, col=c: self.make_move(row, col),
                    bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                    activebackground=self.colors['btn_active'],
                )
                btn.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
                row_buttons.append(btn)
            self.card_buttons.append(row_buttons)

        for c in range(cols):
            game_frame.columnconfigure(c, weight=1)
        for r in range(rows):
            game_frame.rowconfigure(r, weight=1)

        self.root.update_idletasks()
        self._prepare_card_images()

        bottom_frame = tk.Frame(self.root, height=150, bg=self.colors['frame_bg'])
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(5,10))
        bottom_frame.pack_propagate(False)

        chat_frame = tk.Frame(bottom_frame, bg=self.colors['frame_bg'])
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(chat_frame, text="Чат:", font=("Arial", 11),
                 bg=self.colors['frame_bg'], fg=self.colors['fg']).pack(anchor=tk.W)
        self.chat_text = tk.Text(chat_frame, height=5, state=tk.DISABLED,
                                 bg=self.colors['chat_bg'], fg=self.colors['chat_fg'],
                                 insertbackground=self.colors['chat_fg'])
        self.chat_text.pack(fill=tk.BOTH, expand=True, pady=(0,5))

        chat_input_frame = tk.Frame(chat_frame, bg=self.colors['frame_bg'])
        chat_input_frame.pack(fill=tk.X)
        self.chat_entry = tk.Entry(chat_input_frame, font=("Arial", 11),
                                   bg=self.colors['entry_bg'], fg=self.colors['entry_fg'],
                                   insertbackground=self.colors['entry_fg'])
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_entry.bind("<Return>", lambda e: self.send_chat())
        self.chat_entry.focus_set()
        tk.Button(chat_input_frame, text="Отправить", command=self.send_chat,
                  bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                  activebackground=self.colors['btn_active']).pack(side=tk.RIGHT, padx=5)

        control_frame = tk.Frame(bottom_frame, bg=self.colors['frame_bg'])
        control_frame.pack(side=tk.RIGHT, padx=(10,0))
        tk.Button(control_frame, text="Начать заново", command=self.request_restart,
                  bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                  activebackground=self.colors['btn_active']).pack(pady=2)
        tk.Button(control_frame, text="Выход в меню", command=self.confirm_return_to_menu,
                  bg=self.colors['btn_bg'], fg=self.colors['btn_fg'],
                  activebackground=self.colors['btn_active']).pack(pady=2)

    def _prepare_card_images(self) -> None:

        """Создание PhotoImage с master=self.root."""
        if not self.card_buttons or not self.game_state:
            return
        btn = self.card_buttons[0][0]
        w = btn.winfo_width()
        h = btn.winfo_height()
        if w <= 1 or h <= 1:
            self.root.after(50, self._prepare_card_images)
            return

        self.card_photos = []
        self._tk_images = []
        for img_info in self.game_state['card_images']:
            if img_info['type'] == 'image':
                pil_img = Image.open(io.BytesIO(img_info['data']))
                pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(pil_img, master=self.root)
                self._tk_images.append(photo)
                self.card_photos.append(photo)
            else:
                self.card_photos.append(None)
        self.update_board()

    def _show_card_image(self, btn: tk.Button, card_idx: int) -> bool:
        """Показывает картинку на кнопке; при необходимости пересоздаёт PhotoImage."""
        if not self.card_photos or card_idx >= len(self.card_photos):
            return False
        photo = self.card_photos[card_idx]
        if photo is None:
            return False
        try:
            btn.image = photo
            btn.config(image=photo, text='', bg="white", state=tk.NORMAL)
            return True
        except tk.TclError:
            img_info = self.game_state['card_images'][card_idx]
            if img_info.get('type') != 'image' or not img_info.get('data'):
                return False
            w = max(btn.winfo_width(), 48)
            h = max(btn.winfo_height(), 48)
            pil_img = Image.open(io.BytesIO(img_info['data']))
            pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img, master=self.root)
            self._tk_images.append(photo)
            self.card_photos[card_idx] = photo
            btn.image = photo
            btn.config(image=photo, text='', bg="white", state=tk.NORMAL)
            return True

    def update_board(self) -> None:

        """Обновление поля по game_state."""
        if not self.game_state:
            return
        state = self.game_state
        rows, cols = state['rows'], state['cols']
        revealed = state['revealed']
        matched = state['matched']
        board = state['board']
        card_images = state['card_images']

        for r in range(rows):
            for c in range(cols):
                btn = self.card_buttons[r][c]
                if matched[r][c]:
                    btn.config(text="✅", bg=self.colors['win_bg'], state=tk.DISABLED, image='')
                elif revealed[r][c]:
                    card_idx = board[r][c]
                    img_info = card_images[card_idx]
                    if img_info['type'] == 'image' and self._show_card_image(btn, card_idx):
                        pass
                    else:
                        btn.config(
                            text=img_info.get('char', '?'),
                            bg="white", state=tk.NORMAL, image='',
                        )
                else:
                    btn.config(text="❓", bg=self.colors['btn_bg'], state=tk.NORMAL, image='')

        scores = state['scores']
        for i, score in enumerate(scores):
            if i < len(self.score_labels):
                self.score_labels[i].config(text=str(score))

        if state['game_over']:
            winner = state['winner']
            if winner is None:
                self.current_player_label.config(text="Ничья!", bg=self.colors['tie_bg'])
            else:
                name = self.nicknames_all[winner] if winner < len(self.nicknames_all) else f"Игрок {winner+1}"
                self.current_player_label.config(text=f"Победитель: {name}", bg=self.colors['win_bg'])
            self.cancel_timer()
        else:
            cur = state['current_player']
            if cur == self.player_id:
                self.current_player_label.config(text="❗️ ВАШ ХОД!", bg=self.colors['highlight'])
            else:
                name = self.nicknames_all[cur] if cur < len(self.nicknames_all) else f"Игрок {cur+1}"
                self.current_player_label.config(text=f"Ход: {name}", bg=self.colors['top_bg'])

    def add_chat_message(self, sender_name: str, message: str) -> None:

        """Строка в чате."""
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, f"{sender_name}: {message}\n")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def send_chat(self) -> None:

        """Отправка сообщения чата."""
        text = self.chat_entry.get().strip()
        if not text:
            return
        try:
            send_msg(self.client, {'type': 'chat', 'data': text})
            self.chat_entry.delete(0, tk.END)
        except:
            messagebox.showerror("Ошибка", "Не удалось отправить сообщение")

    def make_move(self, row: int, col: int) -> None:

        """Ход (row, col) на сервер."""
        if not self.game_state or self.game_state.get('game_over'):
            messagebox.showinfo("Игра не идёт", "Дождитесь начала игры.")
            return
        if self.game_state['current_player'] != self.player_id:
            messagebox.showinfo("Не ваш ход", "Сейчас не ваша очередь!")
            return
        try:
            send_msg(self.client, {'type': 'move', 'data': (row, col)})
        except:
            messagebox.showerror("Ошибка", "Не удалось отправить ход")

    def request_restart(self) -> None:

        """Голосование за рестарт."""
        try:
            send_msg(self.client, {'type': 'restart_game'})
        except:
            messagebox.showerror("Ошибка", "Не удалось отправить запрос")

    def confirm_return_to_menu(self) -> None:

        """Подтверждение выхода в меню."""
        if messagebox.askyesno("Подтверждение", "Вы уверены, что хотите выйти в главное меню?"):
            self.return_to_menu()

    def return_to_menu(self) -> None:

        """leave_queue и главное меню."""
        self.cancel_timer()
        if self.client:
            try:
                send_msg(self.client, {'type': 'leave_queue'})
            except:
                pass
        self.game_state = None
        self.nicknames_all = []
        self.pending_connect_settings = None
        self.show_main_menu()

    def on_disconnect(self) -> None:

        """Неожиданный обрыв связи."""
        if self._shutting_down:
            return
        self.cancel_timer()
        if self.client:
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None
        self.game_state = None
        self.nicknames_all = []
        self.pending_connect_settings = None
        was_in_session = self.session_active
        self.session_active = False
        if was_in_session:
            messagebox.showwarning("Соединение", "Соединение с сервером потеряно.")
            self.setup_login_screen()
        else:
            self._reset_login_form()

    def on_close(self) -> None:

        """Закрытие по крестику."""
        if messagebox.askyesno("Выход", "Вы уверены, что хотите выйти?"):
            self._finish_quit()

    def run(self) -> None:

        """mainloop окна клиента."""
        self.root.mainloop()


if __name__ == "__main__":
    ensure_runtime_paths()
    gui = MemoryGUI(host='127.0.0.1', port=5555)
    gui.run()
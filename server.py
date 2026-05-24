"""
Серверная часть сетевой игры Memory.

Модуль реализует TCP-сервер (:class:`MemoryServer`), управление лобби
(:class:`GameLobby`), синхронизацию состояния :class:`~utils.MemoryGame`,
игру с ботом, рейтинг и обработку сообщений протокола pickle.

Протокол: 4 байта длины (big-endian) + ``pickle.dumps(dict)``.
Типы сообщений см. обработчик :meth:`MemoryServer.handle_client`.
"""

from __future__ import annotations

import socket
import threading
import pickle
import time
import json
import os
import random
from utils import MemoryGame
from paths import data_path


# Файл накопительного рейтинга игроков (никнейм → очки)
SCORES_FILE = data_path("scores.json")

# Идентификатор виртуального игрока «Бот» в списках lobby.players
BOT_CLIENT_ID = -1


def send_msg(sock: socket.socket, data: object) -> None:
    """
    Отправляет одно сообщение протокола через TCP-сокет.

    Args:
        sock: Подключённый сокет клиента.
        data: Словарь, сериализуемый через ``pickle``.
    """
    msg = pickle.dumps(data)
    length = len(msg)
    sock.sendall(length.to_bytes(4, 'big') + msg)


def recv_msg(sock: socket.socket) -> object:
    """
    Принимает одно сообщение протокола из TCP-сокета.

    Args:
        sock: Подключённый сокет клиента.

    Returns:
        Десериализованный словарь сообщения.

    Raises:
        ConnectionError: Если соединение закрыто до получения полного кадра.
        pickle.PickleError: При ошибке десериализации.
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


class GameLobby:
    """
    Комната ожидания и активная партия для группы игроков.

    Attributes:
        id: Уникальный номер лобби на сервере.
        settings: Параметры партии (размер поля, max_players, turn_time, bot).
        players: Список client_id; ``-1`` — бот.
        started: True после вызова start_game.
        game: Экземпляр :class:`~utils.MemoryGame` или ``None``.
        player_order: Порядок ходов (индексы в ``players``).
        bot_active: Флаг работы потока бота.
        bot_thread: Поток :meth:`MemoryServer.bot_play`.
        bot_memory: Словарь памяти бота: индекс пары → множество (row, col).
    """

    def __init__(self, lobby_id: int, settings: dict) -> None:
        """Создаёт пустое лобби с заданными настройками партии."""
        self.id = lobby_id
        self.settings = settings
        self.players = []
        self.started = False
        self.game: MemoryGame | None = None
        self.player_order = []
        self.bot_active = False
        self.bot_thread = None
        self.bot_memory: dict = {}


class MemoryServer:
    """
    TCP-сервер игры Memory: подключения, лобби, ходы, чат, рейтинг.

    Attributes:
        clients: Соответствие client_id → сокет.
        nicknames: Соответствие client_id → строка никнейма.
        lobbies: Список активных :class:`GameLobby`.
        client_lobby: Соответствие client_id → лобби, в котором состоит клиент.
        lock: Блокировка для потокобезопасного доступа к состоянию.
        scores: Словарь рейтинга (загружается из JSON при старте).
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 5555) -> None:
        """
        Создаёт сокет, привязывает порт и загружает рейтинг.

        Args:
            host: Адрес привязки (``0.0.0.0`` — все интерфейсы).
            port: TCP-порт прослушивания.

        Raises:
            OSError: Если порт занят или привязка невозможна.
        """
        self.host = host
        self.port = port
        self.running = False
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(10)
        self.server.settimeout(1.0)
        print(f"[SERVER] Запущен на {host}:{port}")

        self.clients = {}
        self.nicknames = {}
        self.lobbies = []
        self.client_lobby = {}
        self.lock = threading.Lock()
        self.next_client_id = 1
        self.next_lobby_id = 1
        self.scores = self._load_scores()

    def _load_scores(self) -> dict:
        """Загружает рейтинг из ``scores.json``; при ошибке — пустой словарь."""
        if os.path.exists(SCORES_FILE):
            try:
                with open(SCORES_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_scores(self) -> None:
        """Сохраняет текущий рейтинг в ``scores.json``."""
        with open(SCORES_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.scores, f, indent=2)

    def _update_score(self, nickname: str, points: int) -> None:
        """
        Начисляет очки никнейму и сохраняет рейтинг на диск.

        Args:
            nickname: Имя игрока.
            points: Добавляемые очки (может быть отрицательным).
        """
        if nickname not in self.scores:
            self.scores[nickname] = 0
        self.scores[nickname] += points
        self._save_scores()

    def _get_top_scores(self, limit: int = 5) -> list[tuple[str, int]]:
        """Возвращает топ игроков по убыванию очков."""
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)[:limit]

    def broadcast_top_scores(self) -> None:
        """Рассылает всем подключённым клиентам сообщение ``scores_update``."""
        top = self._get_top_scores(5)
        message = {'type': 'scores_update', 'data': top}
        for cid in self.clients:
            try:
                send_msg(self.clients[cid], message)
            except:
                pass

    def broadcast_lobby(self, lobby: GameLobby, message: dict) -> None:
        """
        Отправляет сообщение всем живым игрокам лобби (кроме бота).

        Args:
            lobby: Целевое лобби.
            message: Словарь протокола (type, data, …).
        """
        for cid in lobby.players:
            if cid == BOT_CLIENT_ID:
                continue
            try:
                send_msg(self.clients[cid], message)
            except:
                pass

    def send_to_client(self, cid: int, message: dict) -> None:
        """Отправляет сообщение одному клиенту; ошибки сокета подавляются."""
        try:
            send_msg(self.clients[cid], message)
        except:
            pass

    def create_lobby(self, settings: dict) -> GameLobby:
        """Создаёт новое лобби с уникальным id и добавляет в список сервера."""
        lobby = GameLobby(self.next_lobby_id, settings)
        self.next_lobby_id += 1
        self.lobbies.append(lobby)
        return lobby

    def remove_from_lobby(self, cid: int) -> None:
        """
        Удаляет клиента из лобби: обновляет очередь или завершает партию.

        При выходе из незавершённой игры оставшимся начисляются очки рейтинга.
        """
        lobby = self.client_lobby.pop(cid, None)
        if lobby is None:
            return
        if not lobby.started:
            lobby.players.remove(cid)
            if not lobby.players:
                if lobby in self.lobbies:
                    self.lobbies.remove(lobby)
            else:
                for other_cid in lobby.players:
                    if other_cid == BOT_CLIENT_ID:
                        continue
                    self.send_to_client(other_cid, {
                        'type': 'queue_update',
                        'message': f'Игроков: {len(lobby.players)}/{lobby.settings["max_players"]}'
                    })
        else:
            if not lobby.game.game_over:
                remaining = [pid for pid in lobby.players if pid != cid]
                for pid in remaining:
                    if pid == BOT_CLIENT_ID:
                        continue
                    self.send_to_client(pid, {
                        'type': 'game_aborted',
                        'message': f'Игрок {self.nicknames.get(cid, "?")} покинул игру. Вы победили!',
                        'winner': self.nicknames.get(pid, 'Вы')
                    })
                    self._update_score(self.nicknames[pid], 3)
            else:
                remaining = [pid for pid in lobby.players if pid != cid]
                for pid in remaining:
                    if pid == BOT_CLIENT_ID:
                        continue
                    self.send_to_client(pid, {
                        'type': 'game_aborted',
                        'message': f'Игрок {self.nicknames.get(cid, "?")} покинул игру. Возврат в меню.'
                    })
            if lobby in self.lobbies:
                self.lobbies.remove(lobby)
            for pid in remaining:
                if pid != BOT_CLIENT_ID:
                    self.client_lobby.pop(pid, None)
        self.broadcast_top_scores()

    def join_game(self, cid: int, settings: dict) -> None:
        """
        Помещает клиента в очередь/лобби по настройкам партии.

        Подбирает существующее лобби с теми же параметрами или создаёт новое.
        При заполнении состава вызывает :meth:`start_game`.

        Args:
            cid: Идентификатор подключённого клиента.
            settings: Словарь с ключами rows, cols, max_players, quick, bot, turn_time.
        """
        settings = dict(settings)
        with self.lock:
            if cid in self.client_lobby:
                self.remove_from_lobby(cid)

            quick = settings.pop('quick', False)
            bot_game = settings.pop('bot', False)
            turn_time = int(settings.pop('turn_time', 0))
            lobby = None

            if quick:
                for l in self.lobbies:
                    if not l.started and len(l.players) < l.settings['max_players']:
                        lobby = l
                        break
                if lobby is None:
                    lobby = self.create_lobby({
                        'rows': settings.get('rows', 4),
                        'cols': settings.get('cols', 4),
                        'max_players': settings.get('max_players', 2),
                        'turn_time': 0,
                        'bot': bot_game
                    })
            else:
                target = {
                    'rows': int(settings['rows']),
                    'cols': int(settings['cols']),
                    'max_players': int(settings['max_players']),
                    'turn_time': turn_time,
                    'bot': bot_game,
                }
                for l in self.lobbies:
                    if (
                        not l.started
                        and l.settings.get('rows') == target['rows']
                        and l.settings.get('cols') == target['cols']
                        and l.settings.get('max_players') == target['max_players']
                        and l.settings.get('turn_time', 0) == target['turn_time']
                        and l.settings.get('bot', False) == target['bot']
                        and len(l.players) < l.settings['max_players']
                    ):
                        lobby = l
                        break
                if lobby is None:
                    lobby = self.create_lobby(target)

            self.client_lobby[cid] = lobby
            lobby.players.append(cid)

            if bot_game and len(lobby.players) == lobby.settings['max_players'] - 1:
                lobby.players.append(BOT_CLIENT_ID)
                self.nicknames[BOT_CLIENT_ID] = "Бот"

            self.send_to_client(cid, {
                'type': 'queue_joined',
                'message': f'Вы в очереди. Игроков: {len(lobby.players)}/{lobby.settings["max_players"]}'
            })
            for other_cid in lobby.players:
                if other_cid != cid and other_cid != BOT_CLIENT_ID:
                    self.send_to_client(other_cid, {
                        'type': 'queue_update',
                        'message': f'Игроков: {len(lobby.players)}/{lobby.settings["max_players"]}'
                    })

            if len(lobby.players) == lobby.settings['max_players']:
                self.start_game(lobby)

    def start_game(self, lobby: GameLobby) -> None:
        """
        Создаёт :class:`~utils.MemoryGame` и рассылает ``game_start`` каждому игроку.

        При наличии бота в составе запускает поток :meth:`bot_play`.
        """
        lobby.player_order = list(lobby.players)
        lobby.game = MemoryGame(
            rows=lobby.settings['rows'],
            cols=lobby.settings['cols'],
            num_players=len(lobby.players)
        )
        lobby.started = True
        lobby.bot_memory = {}  # инициализация общей памяти бота
        turn_time = lobby.settings.get('turn_time', 0)

        for i, cid in enumerate(lobby.player_order):
            if cid == BOT_CLIENT_ID:
                continue
            start_msg = {
                'type': 'game_start',
                'player_id': i,
                'game_state': lobby.game.get_state(),
                'nicknames': [self.nicknames[pid] for pid in lobby.player_order],
                'message': f'Игра началась! Вы игрок {i + 1}.',
                'turn_time': turn_time
            }
            self.send_to_client(cid, start_msg)

        if BOT_CLIENT_ID in lobby.players:
            lobby.bot_active = True
            lobby.bot_thread = threading.Thread(target=self.bot_play, args=(lobby,), daemon=True)
            lobby.bot_thread.start()

    def bot_play(self, lobby: GameLobby) -> None:
        """
        Игровой цикл бота с полной памятью открытых карт.

        Память хранится в ``lobby.bot_memory`` и синхронизируется с ходами людей.
        Бот делает ход, когда ``current_player`` совпадает с индексом бота в ``player_order``.
        """
        while lobby.bot_active and lobby.started and not lobby.game.game_over:
            with self.lock:
                if not lobby.started or lobby.game.game_over:
                    break

                try:
                    bot_index = lobby.player_order.index(BOT_CLIENT_ID)
                except ValueError:
                    break
                if lobby.game.current_player != bot_index:
                    time.sleep(0.2)
                    continue

                memory = lobby.bot_memory

                first_card = lobby.game.first_card
                if first_card is None:
                    # --- Первый выбор ---
                    pair_candidates = []
                    for val, positions in memory.items():
                        available = [pos for pos in positions
                                     if not lobby.game.matched[pos[0]][pos[1]] and not lobby.game.revealed[pos[0]][pos[1]]]
                        if len(available) >= 2:
                            pair_candidates.append(available[0])
                    if pair_candidates:
                        target = random.choice(pair_candidates)
                    else:
                        single_candidates = []
                        for val, positions in memory.items():
                            available = [pos for pos in positions
                                         if not lobby.game.matched[pos[0]][pos[1]] and not lobby.game.revealed[pos[0]][pos[1]]]
                            if available:
                                single_candidates.extend(available)
                        if single_candidates:
                            target = random.choice(single_candidates)
                        else:
                            all_available = [(r, c) for r in range(lobby.game.rows) for c in range(lobby.game.cols)
                                             if not lobby.game.matched[r][c] and not lobby.game.revealed[r][c]]
                            if all_available:
                                target = random.choice(all_available)
                            else:
                                break
                    row, col = target
                else:
                    # --- Второй выбор: ищем пару к уже открытой карте ---
                    first_val = lobby.game.board[first_card[0]][first_card[1]]
                    second = None
                    if first_val in memory:
                        for pos in memory[first_val]:
                            if pos != first_card and not lobby.game.matched[pos[0]][pos[1]] and not lobby.game.revealed[pos[0]][pos[1]]:
                                second = pos
                                break
                    if second is None:
                        available = [(r, c) for r in range(lobby.game.rows) for c in range(lobby.game.cols)
                                     if not lobby.game.matched[r][c] and not lobby.game.revealed[r][c] and (r, c) != first_card]
                        if available:
                            second = random.choice(available)
                    if second:
                        row, col = second
                    else:
                        break

                # Выполняем ход
                success, msg, state = lobby.game.reveal_card(row, col)
                if not success:
                    continue

                # === Обновляем общую память бота ===
                val = lobby.game.board[row][col]
                if val not in memory:
                    memory[val] = set()
                memory[val].add((row, col))

                if lobby.game.matched[row][col]:
                    memory.pop(val, None)
                if lobby.game.first_card is not None:
                    fr, fc = lobby.game.first_card
                    if lobby.game.matched[fr][fc]:
                        fval = lobby.game.board[fr][fc]
                        memory.pop(fval, None)
                # ===

                if state.get('need_close'):
                    self.broadcast_lobby(lobby, {'type': 'game_state', 'data': state, 'message': msg})
                    time.sleep(2)
                    lobby.game.close_unmatched()
                    self.broadcast_lobby(lobby, {
                        'type': 'game_state',
                        'data': lobby.game.get_state(),
                        'message': f"Ход переходит к игроку {lobby.game.current_player + 1}"
                    })
                else:
                    self.broadcast_lobby(lobby, {'type': 'game_state',
                                                'data': lobby.game.get_state(),
                                                'message': msg})
                    if lobby.game.game_over:
                        self.handle_game_over(lobby)
                time.sleep(0.5)

    def handle_timeout(self, cid: int) -> None:
        """
        Обрабатывает истечение таймера хода: закрывает карты и передаёт ход.

        Вызывается при сообщении ``timeout`` от клиента, у которого активен таймер.
        """
        with self.lock:
            lobby = self.client_lobby.get(cid)
            if not lobby or not lobby.started or lobby.game.game_over:
                return
            try:
                player_idx = lobby.player_order.index(cid)
            except ValueError:
                return
            if player_idx != lobby.game.current_player:
                return
            lobby.game.close_unmatched()
            self.broadcast_lobby(lobby, {
                'type': 'game_state',
                'data': lobby.game.get_state(),
                'message': f"Время вышло! Ход переходит к игроку {lobby.game.current_player + 1}"
            })

    def handle_game_over(self, lobby: GameLobby) -> None:
        """
        Начисляет очки рейтинга по результату партии и рассылает обновление топа.

        Победитель получает 3 очка; при ничьей — по 1 очку каждому человеку.
        """
        if not lobby.game or not lobby.game.game_over:
            return
        winner_idx = lobby.game.winner
        if winner_idx is None:
            for cid in lobby.players:
                if cid != BOT_CLIENT_ID:
                    self._update_score(self.nicknames[cid], 1)
        else:
            winner_cid = lobby.player_order[winner_idx]
            if winner_cid != BOT_CLIENT_ID:
                self._update_score(self.nicknames[winner_cid], 3)
        self.broadcast_top_scores()
        # Очистка памяти бота после игры
        lobby.bot_memory = None

    def handle_disconnect(self, cid: int) -> None:
        """Очищает данные клиента при обрыве соединения или выходе из handle_client."""
        with self.lock:
            self.remove_from_lobby(cid)
        self.clients.pop(cid, None)
        self.nicknames.pop(cid, None)
        self.broadcast_top_scores()

    def leave_queue(self, cid: int) -> None:
        """Убирает клиента из очереди лобби без закрытия сокета."""
        with self.lock:
            self.remove_from_lobby(cid)

    def handle_client(self, client: socket.socket, cid: int) -> None:
        """
        Основной цикл обработки сообщений одного подключённого клиента.

        Обрабатывает game_settings, move, chat, timeout, restart_game, leave_queue.
        При разрыве вызывает :meth:`handle_disconnect`.
        """
        while True:
            try:
                message = recv_msg(client)
                msg_type = message.get('type')

                if msg_type == 'game_settings':
                    self.join_game(cid, dict(message.get('data', {})))

                elif msg_type == 'leave_queue':
                    self.leave_queue(cid)

                elif msg_type == 'timeout':
                    self.handle_timeout(cid)

                elif msg_type == 'move':
                    with self.lock:
                        lobby = self.client_lobby.get(cid)
                        if not lobby or not lobby.started or lobby.game.game_over:
                            self.send_to_client(cid, {'type': 'error', 'data': 'Игра не активна'})
                            continue

                        try:
                            player_idx = lobby.player_order.index(cid)
                        except ValueError:
                            self.send_to_client(cid, {'type': 'error', 'data': 'Вы не в игре'})
                            continue

                        if player_idx != lobby.game.current_player:
                            self.send_to_client(cid, {'type': 'error', 'data': 'Сейчас не ваш ход'})
                            continue

                        row, col = message['data']
                        success, msg, state = lobby.game.reveal_card(row, col)
                        if not success:
                            self.send_to_client(cid, {'type': 'error', 'data': msg})
                            continue

                        # === Обновляем общую память бота ===
                        if hasattr(lobby, 'bot_memory') and lobby.bot_memory is not None:
                            val = lobby.game.board[row][col]
                            if val not in lobby.bot_memory:
                                lobby.bot_memory[val] = set()
                            lobby.bot_memory[val].add((row, col))

                            if lobby.game.matched[row][col]:
                                lobby.bot_memory.pop(val, None)
                            if lobby.game.first_card is not None:
                                fr, fc = lobby.game.first_card
                                if lobby.game.matched[fr][fc]:
                                    fval = lobby.game.board[fr][fc]
                                    lobby.bot_memory.pop(fval, None)
                        # === конец обновления памяти ===

                        if state.get('need_close'):
                            self.broadcast_lobby(lobby, {'type': 'game_state', 'data': state, 'message': msg})
                            time.sleep(2)
                            lobby.game.close_unmatched()
                            self.broadcast_lobby(lobby, {
                                'type': 'game_state',
                                'data': lobby.game.get_state(),
                                'message': f"Ход переходит к игроку {lobby.game.current_player + 1}"
                            })
                        else:
                            self.broadcast_lobby(lobby, {'type': 'game_state',
                                                        'data': lobby.game.get_state(),
                                                        'message': msg})
                            if lobby.game.game_over:
                                self.handle_game_over(lobby)

                elif msg_type == 'chat':
                    with self.lock:
                        lobby = self.client_lobby.get(cid)
                        if lobby:
                            self.broadcast_lobby(lobby, {
                                'type': 'chat',
                                'player': cid,
                                'nickname': self.nicknames.get(cid, 'Unknown'),
                                'data': message['data']
                            })

                elif msg_type == 'restart_game':
                    with self.lock:
                        lobby = self.client_lobby.get(cid)
                        if not lobby or not lobby.started:
                            self.send_to_client(cid, {'type': 'error', 'data': 'Игра не активна'})
                            continue

                        if not hasattr(lobby, 'restart_votes'):
                            lobby.restart_votes = set()
                        lobby.restart_votes.add(cid)

                        human_players = [p for p in lobby.players if p != BOT_CLIENT_ID]
                        if len(lobby.restart_votes) == len(human_players):
                            lobby.bot_active = False
                            lobby.game = MemoryGame(
                                rows=lobby.settings['rows'],
                                cols=lobby.settings['cols'],
                                num_players=len(lobby.players)
                            )
                            lobby.started = True
                            lobby.bot_memory = {}  # сброс памяти
                            turn_time = lobby.settings.get('turn_time', 0)
                            for i, pcid in enumerate(lobby.player_order):
                                if pcid == BOT_CLIENT_ID:
                                    continue
                                start_msg = {
                                    'type': 'game_start',
                                    'player_id': i,
                                    'game_state': lobby.game.get_state(),
                                    'nicknames': [self.nicknames[pid] for pid in lobby.player_order],
                                    'message': 'Игра перезапущена!',
                                    'turn_time': turn_time
                                }
                                self.send_to_client(pcid, start_msg)
                            lobby.restart_votes.clear()
                            if BOT_CLIENT_ID in lobby.players:
                                lobby.bot_active = True
                                lobby.bot_thread = threading.Thread(target=self.bot_play, args=(lobby,), daemon=True)
                                lobby.bot_thread.start()

            except (pickle.PickleError, EOFError, ConnectionError):
                break
            except Exception as e:
                print(f"[ERROR] client {cid}: {e}")
                break

        self.handle_disconnect(cid)

    def shutdown(self) -> None:
        """Останавливает сервер и закрывает все подключения."""
        self.running = False
        try:
            self.server.close()
        except OSError:
            pass
        with self.lock:
            for client in list(self.clients.values()):
                try:
                    client.close()
                except OSError:
                    pass
            self.clients.clear()
        print("[SERVER] Остановлен")

    def run(self) -> None:
        """
        Главный цикл сервера: accept, регистрация никнейма, запуск потока клиента.

        Использует ``settimeout(1.0)`` на listen-сокете для возможности
        корректного выхода из цикла через :meth:`shutdown`.
        """
        print("[SERVER] Ожидание подключений...")
        self.running = True
        while self.running:
            try:
                client, addr = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                raise
            with self.lock:
                cid = self.next_client_id
                self.next_client_id += 1
                self.clients[cid] = client
                print(f"[CONNECT] {addr} -> client_id {cid}")

            try:
                send_msg(client, {'type': 'request_nickname', 'client_id': cid})
                message = recv_msg(client)
                if message.get('type') != 'nickname':
                    print(f"[REJECT] client {cid}: first message not nickname")
                    client.close()
                    with self.lock:
                        self.clients.pop(cid, None)
                    continue

                nickname = message['nickname']
                with self.lock:
                    if nickname in self.nicknames.values():
                        send_msg(client, {'type': 'error', 'data': 'Этот никнейм уже используется'})
                        client.close()
                        self.clients.pop(cid, None)
                        continue
                    self.nicknames[cid] = nickname
                    send_msg(client, {
                        'type': 'welcome',
                        'client_id': cid,
                        'message': f'Добро пожаловать, {nickname}!'
                    })
                    top = self._get_top_scores(5)
                    send_msg(client, {'type': 'scores_update', 'data': top})
                    threading.Thread(target=self.handle_client, args=(client, cid), daemon=True).start()

            except Exception as e:
                print(f"[ERROR] Регистрация клиента {cid}: {e}")
                client.close()
                with self.lock:
                    self.clients.pop(cid, None)


if __name__ == '__main__':
    from paths import ensure_runtime_paths
    ensure_runtime_paths()
    server = MemoryServer(host='0.0.0.0', port=5555)
    server.run()
"""
Игровая логика «Мемори» (Memory).

Модуль не зависит от сети и GUI: класс :class:`MemoryGame` хранит состояние поля,
обрабатывает ходы игроков и формирует словарь ``game_state`` для передачи клиентам.
"""

from __future__ import annotations

import random
from io import BytesIO

from PIL import Image

from paths import list_card_image_paths


class MemoryGame:
    """
    Состояние и правила одной партии Memory.

    Attributes:
        rows: Число строк игрового поля.
        cols: Число столбцов игрового поля.
        num_players: Количество участников (индексы 0 … num_players-1).
        board: Матрица индексов пар (значения 0 … num_pairs-1).
        revealed: Матрица флагов «карта открыта лицом вверх».
        matched: Матрица флагов «пара уже собрана».
        scores: Список очков по игрокам.
        current_player: Индекс игрока, чей сейчас ход.
        game_over: Признак окончания партии.
        winner: Индекс победителя или ``None`` при ничьей.
        first_card: Координаты (row, col) первой открытой карты в текущем ходе.
        card_images: Список описаний лиц карт (изображение или эмодзи).
    """

    def __init__(self, rows: int = 4, cols: int = 4, num_players: int = 2) -> None:
        """
        Создаёт новую партию с перемешанным полем.

        Args:
            rows: Количество строк (по умолчанию 4).
            cols: Количество столбцов (по умолчанию 4).
            num_players: Число игроков (не менее 2).

        Raises:
            ValueError: Если ``rows * cols`` нечётно (невозможно разбить на пары).
        """
        self.rows = rows
        self.cols = cols
        self.num_players = num_players
        self.num_cards = self.rows * self.cols
        if self.num_cards % 2 != 0:
            raise ValueError("Количество карт должно быть чётным")

        num_pairs = self.num_cards // 2
        self.card_images = self._load_card_images(num_pairs)
        self.board = self._create_board()
        self.revealed = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        self.matched = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        self.scores = [0] * self.num_players
        self.current_player = 0
        self.game_over = False
        self.winner = None
        self.first_card = None

    def _load_card_images(self, num_pairs: int) -> list[dict]:
        """
        Загружает лица карт из каталога ``card_images`` или подставляет эмодзи.

        Args:
            num_pairs: Требуемое число уникальных пар на поле.

        Returns:
            Список словарей вида ``{'type': 'image', 'data': bytes}`` или
            ``{'type': 'emoji', 'char': str}``. При нехватке файлов изображения
            циклически повторяются.
        """
        file_paths = list_card_image_paths()
        if file_paths:
            images = []
            for i in range(num_pairs):
                path = file_paths[i % len(file_paths)]
                with Image.open(path) as img:
                    img = img.resize((100, 100), Image.Resampling.LANCZOS)
                    buf = BytesIO()
                    img.save(buf, format='PNG')
                    images.append({'type': 'image', 'data': buf.getvalue()})
            return images

        symbols = [
            "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼",
            "🐨", "🐯", "🦁", "🐮", "🐸", "🐙", "🦄", "🐌",
        ]
        return [
            {'type': 'emoji', 'char': symbols[i % len(symbols)]}
            for i in range(num_pairs)
        ]

    def _create_board(self) -> list[list[int]]:
        """
        Формирует и перемешивает поле: по два индекса на каждую пару.

        Returns:
            Двумерный список ``rows × cols`` с индексами в ``card_images``.
        """
        cards = []
        for i in range(len(self.card_images)):
            cards.extend([i, i])
        random.shuffle(cards)

        board = []
        idx = 0
        for _ in range(self.rows):
            row = []
            for _ in range(self.cols):
                row.append(cards[idx])
                idx += 1
            board.append(row)
        return board

    def reveal_card(self, row: int, col: int) -> tuple[bool, str, dict | None]:
        """
        Обрабатывает открытие карты в координатах (row, col).

        Args:
            row: Номер строки (0-based).
            col: Номер столбца (0-based).

        Returns:
            Кортеж ``(успех, сообщение, состояние)``. При неуспехе ``состояние`` — ``None``.
            В состоянии может быть ключ ``need_close``: клиентам нужно показать обе
            карты и затем вызвать :meth:`close_unmatched`.
        """
        if self.game_over:
            return False, "Игра уже окончена", None
        if self.matched[row][col]:
            return False, "Эта карта уже убрана", None
        if self.revealed[row][col]:
            return False, "Карта уже открыта", None

        self.revealed[row][col] = True

        if self.first_card is None:
            self.first_card = (row, col)
            return True, "Первая карта открыта", self.get_state()

        first_row, first_col = self.first_card
        val1 = self.board[first_row][first_col]
        val2 = self.board[row][col]

        if val1 == val2:
            self.matched[first_row][first_col] = True
            self.matched[row][col] = True
            self.scores[self.current_player] += 1
            self.first_card = None
            if self._check_win():
                self._set_winner()
                self.game_over = True
                return True, f"Игрок {self.current_player + 1} выиграл!", self.get_state()
            return True, "Пара найдена! +1 очко, ход продолжается", self.get_state()

        state = self.get_state()
        state['need_close'] = True
        return True, "Не совпали! Ход переходит", state

    def close_unmatched(self) -> None:
        """
        Закрывает все открытые, но не совпавшие карты и передаёт ход следующему игроку.

        Вызывается сервером после паузы, когда клиенты успели увидеть обе карты.
        """
        for r in range(self.rows):
            for c in range(self.cols):
                if self.revealed[r][c] and not self.matched[r][c]:
                    self.revealed[r][c] = False
        self.first_card = None
        self.current_player = (self.current_player + 1) % self.num_players

    def _check_win(self) -> bool:
        """Проверяет, собраны ли все пары на поле."""
        for r in range(self.rows):
            for c in range(self.cols):
                if not self.matched[r][c]:
                    return False
        return True

    def _set_winner(self) -> None:
        """
        Определяет победителя по максимальному числу очков.

        При равенстве очков у нескольких игроков ``winner`` остаётся ``None`` (ничья).
        """
        max_score = max(self.scores)
        winners = [i for i, s in enumerate(self.scores) if s == max_score]
        self.winner = winners[0] if len(winners) == 1 else None

    def get_state(self) -> dict:
        """
        Сериализует полное состояние партии для отправки по сети.

        Returns:
            Словарь с ключами board, revealed, matched, scores, current_player,
            game_over, winner, rows, cols, card_images.
        """
        return {
            "board": self.board,
            "revealed": self.revealed,
            "matched": self.matched,
            "scores": self.scores,
            "current_player": self.current_player,
            "game_over": self.game_over,
            "winner": self.winner,
            "rows": self.rows,
            "cols": self.cols,
            "card_images": self.card_images,
        }

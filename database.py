"""Database module for the vocabulary application.

This module encapsulates all access to the underlying SQLite database. It
defines a `Database` class which manages connection lifecycle, schema
creation and provides convenience methods for common operations such as
inserting languages, translation groups, vocabulary items and sets (lessons).

The schema is designed to be flexible enough to support an arbitrary number
of languages. Words are grouped together via the `translation_groups` table;
each group can contain multiple vocabulary entries (one per language). Sets
group together translation groups for a particular lesson or import.

Using a dedicated class rather than exposing raw SQL calls helps to keep
the rest of the code decoupled from the underlying storage engine. Should
you wish to migrate to another database in the future, only this file
needs to be updated.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Dict, Tuple


class Database:
    """Lightweight wrapper around SQLite with application specific helpers."""

    def __init__(self, db_path: str = "glosprogram.db") -> None:
        self.db_path = db_path
        # Connect with isolation_level=None to enable autocommit. This
        # simplifies transaction handling for a small application like this.
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        # Return rows as dictionaries for convenience.
        self.conn.row_factory = sqlite3.Row
        self._create_schema()
        self._ensure_default_languages()

    def _create_schema(self) -> None:
        """Create all tables if they do not already exist."""
        cur = self.conn.cursor()
        # Languages: unique name and optional code. Code isn't strictly
        # enforced but helpful when interfacing with translation APIs.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS languages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                code TEXT
            )
            """
        )
        # Translation groups: each group represents a concept across languages.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """
        )
        # Vocabulary items: one entry per word in a specific language.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vocab_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                language_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                import_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (group_id, language_id),
                FOREIGN KEY (group_id) REFERENCES translation_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (language_id) REFERENCES languages(id) ON DELETE CASCADE
            )
            """
        )
        # Sets / lessons table.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Relationship between sets and translation groups (many-to-many).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS set_groups (
                set_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                PRIMARY KEY (set_id, group_id),
                FOREIGN KEY (set_id) REFERENCES sets(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES translation_groups(id) ON DELETE CASCADE
            )
            """
        )

        # Tags for sets (many-to-many). Tags enable categorisation and filtering.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS set_tags (
                set_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (set_id, tag_id),
                FOREIGN KEY (set_id) REFERENCES sets(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
            """
        )

        # Quiz sessions: summarises each quiz attempt.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id INTEGER NOT NULL,
                session_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_questions INTEGER NOT NULL,
                correct_answers INTEGER NOT NULL,
                FOREIGN KEY (set_id) REFERENCES sets(id) ON DELETE CASCADE
            )
            """
        )
        # Quiz answers: detailed log of each answer in a session.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                from_lang TEXT NOT NULL,
                to_lang TEXT NOT NULL,
                correct INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES translation_groups(id) ON DELETE CASCADE
            )
            """
        )

        # Users table for multi-user support
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL
            )
            """
        )
        # Ensure quiz_sessions has a user_id column; attempt to add if missing
        try:
            cur.execute("ALTER TABLE quiz_sessions ADD COLUMN user_id INTEGER")
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Table to track spaced repetition progress for each user and group.
        # Each record stores a box number (Leitner system) and a due_date
        # indicating when the card should next be reviewed. When the user
        # answers correctly, the box increases and the due_date is advanced
        # exponentially. Incorrect answers reset the box and schedule the
        # card for immediate review. See the Leitner system description for
        # details on how cards move between boxes and how the intervals grow【457647381170†L156-L165】.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                box INTEGER DEFAULT 0,
                due_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES translation_groups(id) ON DELETE CASCADE
            )
            """
        )

    def _ensure_default_languages(self) -> None:
        """Insert a few common languages on first run.

        The application expects Swedish, English and Spanish to be present
        by default. Additional languages will be inserted on demand.
        """
        default_langs = [
            ("Swedish", "sv"),
            ("English", "en"),
            ("Spanish", "es"),
        ]
        for name, code in default_langs:
            self.get_language_id(name, code)

    # Public API methods
    def get_language_id(self, name: str, code: Optional[str] = None) -> int:
        """Return the ID for a given language, inserting it if necessary.

        Args:
            name: Human readable language name (e.g. "Swedish").
            code: Optional two-letter ISO code. If provided on first insert,
                  it will be stored in the database. On subsequent calls,
                  the code parameter is ignored.

        Returns:
            The integer ID associated with the language.
        """
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM languages WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        # Insert new language
        cur.execute(
            "INSERT INTO languages (name, code) VALUES (?, ?)", (name, code)
        )
        return int(cur.lastrowid)

    def list_languages(self) -> List[Dict[str, str]]:
        """Return all languages sorted by name."""
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, code FROM languages ORDER BY name")
        return [dict(row) for row in cur.fetchall()]

    def create_set(self, name: str, description: str = "") -> int:
        """Create a new set (lesson) and return its ID."""
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO sets (name, description) VALUES (?, ?)",
            (name, description),
        )
        return int(cur.lastrowid)

    def add_group(self) -> int:
        """Create a new translation group and return its ID."""
        cur = self.conn.cursor()
        cur.execute("INSERT INTO translation_groups DEFAULT VALUES")
        return int(cur.lastrowid)

    def add_vocab_item(self, group_id: int, language: str, word: str) -> None:
        """Add a word in a particular language to a translation group.

        If the language doesn't exist, it will be inserted. If an item
        already exists for the (group_id, language) pair, this call will
        silently ignore the new word.

        Args:
            group_id: The translation group the word belongs to.
            language: The name of the language (e.g. "Swedish").
            word: The actual vocabulary item.
        """
        lang_id = self.get_language_id(language)
        cur = self.conn.cursor()
        # Ignore duplicates by using INSERT OR IGNORE.
        cur.execute(
            """
            INSERT OR IGNORE INTO vocab_items (group_id, language_id, word)
            VALUES (?, ?, ?)
            """,
            (group_id, lang_id, word),
        )

    def add_group_to_set(self, set_id: int, group_id: int) -> None:
        """Associate a translation group with a set."""
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO set_groups (set_id, group_id) VALUES (?, ?)",
            (set_id, group_id),
        )

    # Tag-related methods
    def get_tag_id(self, name: str) -> int:
        """Return the ID for a tag, inserting it if necessary."""
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM tags WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute("INSERT INTO tags (name) VALUES (?)", (name,))
        return int(cur.lastrowid)

    def add_tag_to_set(self, set_id: int, tag_name: str) -> None:
        """Associate a tag with a set."""
        tag_id = self.get_tag_id(tag_name)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO set_tags (set_id, tag_id) VALUES (?, ?)",
            (set_id, tag_id),
        )

    # User-related methods
    def get_user_id(self, username: str) -> int:
        """Get or create a user by username."""
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute("INSERT INTO users (username) VALUES (?)", (username,))
        return int(cur.lastrowid)

    def list_users(self) -> List[Dict[str, str]]:
        """Return all users sorted by username."""
        cur = self.conn.cursor()
        cur.execute("SELECT id, username FROM users ORDER BY username")
        return [dict(row) for row in cur.fetchall()]

    # Quiz session logging
    def record_quiz_session(
        self,
        set_id: int,
        total: int,
        correct: int,
        user_id: Optional[int] = None,
    ) -> int:
        """Insert a quiz session summary and return its ID.

        Args:
            set_id: ID of the lesson.
            total: Total number of questions asked in this session.
            correct: Number of correct answers.
            user_id: Optional ID of the user who took the quiz.
        """
        cur = self.conn.cursor()
        if user_id is not None:
            cur.execute(
                """
                INSERT INTO quiz_sessions (set_id, total_questions, correct_answers, user_id)
                VALUES (?, ?, ?, ?)
                """,
                (set_id, total, correct, user_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO quiz_sessions (set_id, total_questions, correct_answers)
                VALUES (?, ?, ?)
                """,
                (set_id, total, correct),
            )
        return int(cur.lastrowid)

    def record_quiz_answer(
        self,
        session_id: int,
        group_id: int,
        from_lang: str,
        to_lang: str,
        correct: bool,
    ) -> None:
        """Insert a detailed record for a single quiz answer."""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO quiz_answers (session_id, group_id, from_lang, to_lang, correct)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, group_id, from_lang, to_lang, 1 if correct else 0),
        )

    # Statistics and analytics
    def get_problematic_groups(
        self,
        set_id: int,
        threshold: float = 0.7,
        since_days: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[int]:
        """Return group IDs with success ratio below threshold.

        Args:
            set_id: Lesson to analyse.
            threshold: Correctness ratio below which a word is considered problematic.
            since_days: Optional number of days to look back; if provided, only
                sessions newer than this many days are considered.

        Returns:
            A list of group IDs.
        """
        cur = self.conn.cursor()
        params = [set_id]
        date_filter = ""
        user_filter = ""
        if since_days is not None:
            date_filter = "AND s.session_time >= datetime('now', ? )"
            params.append(f'-{since_days} day')
        if user_id is not None:
            user_filter = "AND s.user_id = ?"
            params.append(user_id)
        # Aggregate correctness by group
        sql = f"""
            SELECT a.group_id,
                   SUM(a.correct) AS correct_count,
                   COUNT(*) AS total_count
            FROM quiz_answers a
            JOIN quiz_sessions s ON a.session_id = s.id
            WHERE s.set_id = ? {date_filter} {user_filter}
            GROUP BY a.group_id
            HAVING (CAST(correct_count AS FLOAT) / total_count) < ?
        """
        params.append(threshold)
        cur.execute(sql, params)
        return [int(row["group_id"]) for row in cur.fetchall()]

    def get_stats(
        self, set_id: int, since_days: Optional[int] = None, user_id: Optional[int] = None
    ) -> Dict[str, float]:
        """Compute aggregate statistics for a set.

        Args:
            set_id: Lesson to analyse.
            since_days: Optional timeframe in days.

        Returns:
            A dict containing total_sessions, avg_score, avg_correct_ratio.
        """
        cur = self.conn.cursor()
        params = [set_id]
        date_filter = ""
        user_filter = ""
        if since_days is not None:
            date_filter = "AND session_time >= datetime('now', ? )"
            params.append(f'-{since_days} day')
        if user_id is not None:
            user_filter = "AND user_id = ?"
            params.append(user_id)
        cur.execute(
            f"""
            SELECT COUNT(*) AS session_count,
                   AVG(correct_answers) AS avg_correct,
                   AVG(CAST(correct_answers AS FLOAT) / total_questions) AS avg_ratio
            FROM quiz_sessions
            WHERE set_id = ? {date_filter} {user_filter}
            """,
            params,
        )
        row = cur.fetchone()
        if not row or row["session_count"] is None:
            return {"total_sessions": 0, "avg_correct": 0.0, "avg_ratio": 0.0}
        return {
            "total_sessions": int(row["session_count"]),
            "avg_correct": float(row["avg_correct"] or 0.0),
            "avg_ratio": float(row["avg_ratio"] or 0.0),
        }

    # Spaced repetition utilities
    def update_user_progress(self, user_id: int, group_id: int, correct: bool) -> None:
        """Update spaced repetition progress for a user and group.

        Implements a simple Leitner-style scheduling algorithm. Each card
        (translation group) has a box number which determines the interval
        before the next review. A correct answer moves the card to the next
        box (capped at 5), doubling the interval. An incorrect answer resets
        the card to box 0 for immediate review. The due_date is updated
        accordingly. Intervals are measured in whole days (2**box days)【457647381170†L156-L165】.

        Args:
            user_id: The ID of the user.
            group_id: The translation group ID.
            correct: True if the user's answer was correct.
        """
        import datetime
        cur = self.conn.cursor()
        cur.execute(
            "SELECT box FROM user_progress WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        row = cur.fetchone()
        # Determine new box
        if row:
            box = int(row["box"])
            if correct:
                box = min(box + 1, 5)
            else:
                box = 0
            # Compute new due date based on box (2**box days)
            interval_days = 2 ** box
            due_date = datetime.datetime.now() + datetime.timedelta(days=interval_days)
            cur.execute(
                "UPDATE user_progress SET box = ?, due_date = ? WHERE user_id = ? AND group_id = ?",
                (box, due_date, user_id, group_id),
            )
        else:
            # New record: start in box 0 or 1 depending on correctness
            box = 1 if correct else 0
            interval_days = 2 ** box
            due_date = datetime.datetime.now() + datetime.timedelta(days=interval_days)
            cur.execute(
                "INSERT INTO user_progress (user_id, group_id, box, due_date) VALUES (?, ?, ?, ?)",
                (user_id, group_id, box, due_date),
            )

    def get_due_groups(self, set_id: int, user_id: int) -> List[int]:
        """Return group IDs from a set that are due for review for a user.

        A group is due if its due_date is in the past or it has never been
        studied. This function ensures that new cards are introduced along
        with scheduled reviews.

        Args:
            set_id: The lesson ID.
            user_id: The user ID.

        Returns:
            A list of group IDs ready for review.
        """
        cur = self.conn.cursor()
        # Select groups in the set where either no progress record exists or the due_date has passed
        cur.execute(
            """
            SELECT sg.group_id
            FROM set_groups sg
            LEFT JOIN user_progress up
              ON sg.group_id = up.group_id AND up.user_id = ?
            WHERE sg.set_id = ?
              AND (up.due_date IS NULL OR up.due_date <= CURRENT_TIMESTAMP)
            """,
            (user_id, set_id),
        )
        return [int(row["group_id"]) for row in cur.fetchall()]

    def get_distractors(
        self,
        set_id: int,
        exclude_group_id: int,
        target_language: str,
        num_choices: int = 3,
    ) -> List[str]:
        """Return a list of distractor words for multiple-choice questions.

        Distractors are drawn from the same set but from different groups. Only
        groups containing the target language are considered. If there are
        insufficient distinct distractors, the list may contain fewer items.

        Args:
            set_id: The lesson ID.
            exclude_group_id: The correct group's ID to exclude.
            target_language: The language of the answer.
            num_choices: The number of distractors desired (default 3).

        Returns:
            A list of words (strings) to use as incorrect options.
        """
        group_ids = self.fetch_set_group_ids(set_id)
        # Remove the correct group from candidates
        group_ids = [gid for gid in group_ids if gid != exclude_group_id]
        import random
        random.shuffle(group_ids)
        distractors: List[str] = []
        for gid in group_ids:
            words = self.fetch_group_words(gid)
            if target_language in words and words[target_language] not in distractors:
                distractors.append(words[target_language])
            if len(distractors) >= num_choices:
                break
        return distractors

    # Query methods
    def fetch_group_words(self, group_id: int) -> Dict[str, str]:
        """Return a mapping from language name to word for a group."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT l.name AS language, v.word
            FROM vocab_items v
            JOIN languages l ON v.language_id = l.id
            WHERE v.group_id = ?
            """,
            (group_id,),
        )
        return {row["language"]: row["word"] for row in cur.fetchall()}

    def fetch_set_group_ids(self, set_id: int) -> List[int]:
        """Return a list of translation group IDs belonging to a set."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT group_id FROM set_groups WHERE set_id = ?",
            (set_id,),
        )
        return [int(row["group_id"]) for row in cur.fetchall()]

    def fetch_sets(self) -> List[Dict[str, str]]:
        """Retrieve all sets, sorted by import date descending."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, name, description, import_date FROM sets ORDER BY import_date DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    def fetch_set_translations(
        self,
        set_id: int,
        lang_from: str,
        lang_to: str,
    ) -> List[Tuple[str, str]]:
        """Return all translations for a set from one language to another.

        This helper is primarily used by quiz logic. If a word is missing
        a translation in the target language, that entry is skipped.

        Args:
            set_id: The lesson to fetch translations from.
            lang_from: Source language name.
            lang_to: Target language name.

        Returns:
            A list of tuples (source_word, target_word).
        """
        cur = self.conn.cursor()
        # Fetch all group ids once up front to avoid nested queries.
        group_ids = self.fetch_set_group_ids(set_id)
        if not group_ids:
            return []
        placeholders = ",".join(["?"] * len(group_ids))
        # SQL to pivot two joins: one for each language. We join
        # vocab_items twice on the same group_id to fetch the source and
        # target words.
        sql = f"""
            SELECT s.word AS source_word, t.word AS target_word
            FROM vocab_items s
            JOIN vocab_items t ON s.group_id = t.group_id
            JOIN languages sl ON s.language_id = sl.id
            JOIN languages tl ON t.language_id = tl.id
            WHERE s.group_id IN ({placeholders})
              AND sl.name = ? AND tl.name = ?
        """
        cur.execute(
            sql,
            (*group_ids, lang_from, lang_to),
        )
        return [(row["source_word"], row["target_word"]) for row in cur.fetchall()]

    def fetch_random_group_and_direction(
        self,
        set_id: int,
        languages: Optional[List[str]] = None,
        random_direction: bool = False,
        allowed_group_ids: Optional[List[int]] = None,
    ) -> Optional[Tuple[int, str, str, str, str]]:
        """Pick a random translation from the set.

        Args:
            set_id: Lesson ID to draw words from.
            languages: Optional list of exactly two languages to quiz between.
                If omitted, the method picks any available language pair.
            random_direction: If True, the direction (from->to) will be
                chosen randomly between the two provided languages.
            allowed_group_ids: Optional list of group IDs to restrict the pool.

        Returns:
            A tuple (group_id, source_language, source_word, target_language, target_word),
            or None if no valid translation exists.
        """
        group_ids = self.fetch_set_group_ids(set_id)
        if allowed_group_ids is not None:
            group_ids = [gid for gid in group_ids if gid in allowed_group_ids]
        if not group_ids:
            return None
        import random
        random.shuffle(group_ids)
        for group_id in group_ids:
            words = self.fetch_group_words(group_id)
            if not words:
                continue
            # Determine candidate language pairs
            if languages:
                if len(languages) != 2:
                    raise ValueError("languages must be a list of exactly two names")
                lang1, lang2 = languages
                if lang1 not in words or lang2 not in words:
                    continue
                options = [(lang1, lang2)]
            else:
                langs = list(words.keys())
                if len(langs) < 2:
                    continue
                options = []
                for i in range(len(langs)):
                    for j in range(len(langs)):
                        if i != j:
                            options.append((langs[i], langs[j]))
            random.shuffle(options)
            for (src, tgt) in options:
                if src in words and tgt in words:
                    if random_direction and languages:
                        if random.choice([True, False]):
                            src, tgt = tgt, src
                    return (group_id, src, words[src], tgt, words[tgt])
        return None
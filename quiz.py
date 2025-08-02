"""Quiz logic module.

This module builds on top of the database to provide quiz functionality.
It defines a `Quiz` class capable of generating questions from sets
(lessons) and checking user answers. The quiz logic operates purely on
Python data structures, leaving presentation (UI) to the caller.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List

from .database import Database


@dataclass
class Question:
    set_id: int
    group_id: int
    source_language: str
    source_word: str
    target_language: str
    target_word: str

    def check_answer(self, answer: str) -> bool:
        return answer.strip().lower() == self.target_word.strip().lower()


class Quiz:
    def __init__(self, db: Database) -> None:
        self.db = db

    def generate_question(
        self,
        set_id: int,
        languages: Optional[List[str]] = None,
        random_direction: bool = False,
        allowed_group_ids: Optional[List[int]] = None,
    ) -> Optional[Question]:
        """Generate a single quiz question.

        Args:
            set_id: The lesson to draw from.
            languages: Optional list specifying exactly two languages to quiz
                between. If None, any available language pair will be chosen.
            random_direction: If True and languages is not None, randomly
                swap the direction of the pair.

        Returns:
            A `Question` instance or None if no suitable data is available.
        """
        result = self.db.fetch_random_group_and_direction(
            set_id,
            languages,
            random_direction,
            allowed_group_ids,
        )
        if result is None:
            return None
        group_id, src_lang, src_word, tgt_lang, tgt_word = result
        return Question(
            set_id=set_id,
            group_id=group_id,
            source_language=src_lang,
            source_word=src_word,
            target_language=tgt_lang,
            target_word=tgt_word,
        )

    def quiz_session(
        self,
        set_id: int,
        num_questions: int = 10,
        languages: Optional[List[str]] = None,
        random_direction: bool = True,
    ) -> List[Tuple[Question, bool]]:
        """Run a quiz session in memory.

        Args:
            set_id: The lesson to quiz on.
            num_questions: Number of questions to ask.
            languages: Optional list of exactly two languages for the quiz.
            random_direction: If True, randomize the direction of questions.

        Returns:
            A list of tuples containing each `Question` and a boolean
            indicating whether the user's answer was correct. This method
            is intended for non-UI usage (e.g. CLI). UI layers should
            implement their own event loops and make use of
            `generate_question` directly.
        """
        results: List[Tuple[Question, bool]] = []
        for _ in range(num_questions):
            q = self.generate_question(set_id, languages, random_direction)
            if q is None:
                break
            # For CLI demonstration, we prompt the user here. In a GUI
            # application you would instead render the question and collect
            # user input via forms.
            print(f"Translate '{q.source_word}' from {q.source_language} to {q.target_language}:")
            try:
                answer = input().strip()
            except EOFError:
                answer = ""
            correct = q.check_answer(answer)
            results.append((q, correct))
            print("Correct!" if correct else f"Wrong. Correct answer: {q.target_word}")
        return results
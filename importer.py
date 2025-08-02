"""Importer module for vocabulary lists.

This module is responsible for parsing user supplied data and populating
the database with translation groups. It attempts to support a variety
of common formats out of the box: tab-separated lists, comma separated
lists and simple Markdown tables. Rows with an uneven number of columns
are ignored. Empty lines and comment lines are skipped.

On import, a new set (lesson) is created in the database. Each row in
the input is turned into a translation group containing one entry per
column/language. Optionally, a third language can be filled in using
an external translation service via the `translator` module. For
example, if the user imports English/Swedish pairs and requests
Spanish completion, the importer will ask the translator to translate
the English term to Spanish.
"""

from __future__ import annotations

import csv
import re
from typing import List, Optional

from .database import Database
from .translator import BaseTranslator, get_default_translator


class Importer:
    """Handles importing vocab lists into the database."""

    def __init__(
        self,
        db: Database,
        translator: Optional[BaseTranslator] = None,
        default_main_language: str = "Swedish",
    ) -> None:
        self.db = db
        self.translator = translator or get_default_translator()
        self.default_main_language = default_main_language

    def _detect_delimiter(self, sample: str) -> str:
        """Heuristically determine the column delimiter in the text.

        The importer supports tab-separated, comma-separated and pipe-separated
        (Markdown tables) formats. If the text contains pipe characters, we
        assume a Markdown table and strip the outer pipes. Otherwise we look
        for tabs; failing that, commas.
        """
        if "|" in sample:
            return "|"
        elif "\t" in sample:
            return "\t"
        elif ";" in sample:
            return ";"
        else:
            return ","

    def _parse_lines(self, text: str) -> List[List[str]]:
        """Parse the raw text into a list of rows (lists of strings).

        Lines beginning with '#' or '|' (header separators in Markdown tables)
        are ignored. Leading and trailing whitespace on each cell is
        stripped.
        """
        # Remove code fences if present
        clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        lines = [line.strip() for line in clean.strip().splitlines() if line.strip()]
        if not lines:
            return []
        delim = self._detect_delimiter(lines[0])
        rows: List[List[str]] = []
        for line in lines:
            if line.startswith("#"):
                continue
            # Skip markdown header separators
            if re.match(r"\|?\s*:-", line):
                continue
            if delim == "|":
                # Remove outer pipes and split
                tokens = [cell.strip() for cell in line.strip("|").split("|")]
            else:
                tokens = [cell.strip() for cell in line.split(delim)]
            # Filter out empty strings resulting from consecutive delimiters
            tokens = [t for t in tokens if t]
            if len(tokens) < 2:
                # We need at least two columns to form a translation pair
                continue
            rows.append(tokens)
        return rows

    def import_from_string(
        self,
        set_name: str,
        text: str,
        languages_order: Optional[List[str]] = None,
        auto_translate_spanish: bool = False,
        tags: Optional[List[str]] = None,
    ) -> int:
        """Import vocabulary from a raw string.

        Args:
            set_name: Name of the lesson to create.
            text: Raw text containing rows of vocabulary.
            languages_order: Optional list specifying the language of each
                column. If omitted, the first two columns will be assumed
                to be [source_language, main_language].
            auto_translate_spanish: If True, attempt to fill in Spanish
                translations for each row via the translator module. The
                Spanish translation will be derived from the first language
                in `languages_order` or the first column if unspecified.

        Returns:
            The ID of the newly created set.
        """
        # Create a new set
        set_id = self.db.create_set(set_name)
        # Associate tags if provided
        if tags:
            for tag in tags:
                self.db.add_tag_to_set(set_id, tag.strip().title())
        rows = self._parse_lines(text)
        if not rows:
            return set_id
        # Determine languages for columns. We use a single language order for
        # all rows. If the caller provided `languages_order` matching the
        # number of columns, we trust it. Otherwise we construct a default
        # order: the first column is treated as an unknown source language,
        # the second column is treated as the main (target) language, and
        # any additional columns are labelled Unknown3, Unknown4 etc. Using
        # unknown language names allows the database to insert new language
        # entries automatically, preserving all information for later
        # correction.
        if languages_order and len(languages_order) == len(rows[0]):
            # Normalize capitalization
            languages_order = [lang.strip().title() for lang in languages_order]
        else:
            num_cols = len(rows[0])
            inferred: List[str] = []
            for i in range(num_cols):
                if i == 0:
                    inferred.append("Unknown1")
                elif i == 1:
                    inferred.append(self.default_main_language)
                else:
                    inferred.append(f"Unknown{i+1}")
            languages_order = inferred

        # Iterate through all rows and insert into DB
        for cols in rows:
            if len(cols) != len(languages_order):
                # Skip rows with unexpected number of columns
                continue
            group_id = self.db.add_group()
            for word, lang in zip(cols, languages_order):
                self.db.add_vocab_item(group_id, lang, word)
            # Optionally add Spanish translation
            if auto_translate_spanish:
                try:
                    # Determine source language to translate from: take the first provided language
                    src_lang = languages_order[0]
                    # Translate only if we don't already have a Spanish column
                    if "Spanish" not in languages_order:
                        src_code = None
                        dest_code = None
                        # Attempt to map language names to codes via the database
                        lang_map = {lr["name"]: lr["code"] for lr in self.db.list_languages()}
                        if src_lang in lang_map and lang_map[src_lang]:
                            src_code = lang_map[src_lang]
                        if "Spanish" in lang_map and lang_map["Spanish"]:
                            dest_code = lang_map["Spanish"]
                        if src_code and dest_code:
                            translation = self.translator.translate(cols[0], src=src_code, dest=dest_code)
                            if translation:
                                self.db.add_vocab_item(group_id, "Spanish", translation)
                except Exception:
                    pass
            # Associate group with set
            self.db.add_group_to_set(set_id, group_id)
        return set_id

    def import_into_set(
        self,
        set_id: int,
        text: str,
        languages_order: Optional[List[str]] = None,
        auto_translate_spanish: bool = False,
    ) -> None:
        """Import vocabulary into an existing set.

        This method behaves like ``import_from_string`` but does not
        create a new set. It simply parses the text and attaches
        the created translation groups to the provided ``set_id``.

        Args:
            set_id: The ID of the existing lesson to append words to.
            text: Raw text containing vocabulary rows.
            languages_order: Optional list specifying the language of each
                column. Follows the same conventions as
                ``import_from_string``.
            auto_translate_spanish: If True, attempt to fill in Spanish
                translations for each row via the translator module.

        Returns:
            None. The existing set will be updated in place.
        """
        rows = self._parse_lines(text)
        if not rows:
            return
        # Determine languages order similarly to import_from_string
        if languages_order and len(languages_order) == len(rows[0]):
            languages_order = [lang.strip().title() for lang in languages_order]
        else:
            num_cols = len(rows[0])
            inferred: List[str] = []
            for i in range(num_cols):
                if i == 0:
                    inferred.append("Unknown1")
                elif i == 1:
                    inferred.append(self.default_main_language)
                else:
                    inferred.append(f"Unknown{i+1}")
            languages_order = inferred
        for cols in rows:
            if len(cols) != len(languages_order):
                continue
            group_id = self.db.add_group()
            for word, lang in zip(cols, languages_order):
                self.db.add_vocab_item(group_id, lang, word)
            if auto_translate_spanish:
                try:
                    src_lang = languages_order[0]
                    if "Spanish" not in languages_order:
                        src_code = None
                        dest_code = None
                        lang_map = {lr["name"]: lr["code"] for lr in self.db.list_languages()}
                        if src_lang in lang_map and lang_map[src_lang]:
                            src_code = lang_map[src_lang]
                        if "Spanish" in lang_map and lang_map["Spanish"]:
                            dest_code = lang_map["Spanish"]
                        if src_code and dest_code:
                            translation = self.translator.translate(cols[0], src=src_code, dest=dest_code)
                            if translation:
                                self.db.add_vocab_item(group_id, "Spanish", translation)
                except Exception:
                    pass
            self.db.add_group_to_set(set_id, group_id)
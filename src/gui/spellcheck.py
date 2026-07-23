from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtCore import Qt
from spellchecker import SpellChecker
import re

class SpellCheckHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spell = SpellChecker(language='en')
        import os
        dict_path = '/usr/share/dict/british-english'
        if os.path.exists(dict_path):
            try:
                self.spell.word_frequency.load_text_file(dict_path)
            except Exception as e:
                import logging
                logging.warning(f"Could not load British English dictionary: {e}")
        
        # Format for misspelled words
        self.error_format = QTextCharFormat()
        self.error_format.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        self.error_format.setUnderlineColor(QColor("red"))

    def highlightBlock(self, text):
        if not text:
            return

        # Split text into words, removing punctuation
        words = re.finditer(r'\b[a-zA-Z]+\b', text)
        
        for match in words:
            word = match.group()
            # If word is misspelled
            if word.lower() not in self.spell:
                # spellchecker sometimes doesn't like single characters or specific cases
                # we do a basic check
                if len(word) > 1 and word not in self.spell.known([word.lower()]):
                    self.setFormat(match.start(), match.end() - match.start(), self.error_format)

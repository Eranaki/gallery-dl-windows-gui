from __future__ import annotations


def txt(language: str, ru: str, en: str) -> str:
    return ru if language == "ru" else en

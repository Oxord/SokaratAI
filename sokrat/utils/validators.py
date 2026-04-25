from __future__ import annotations

ROLES: list[str] = ["backend", "frontend", "data_engineer", "ml_engineer", "qa"]
LEVELS: list[str] = ["junior", "middle", "senior"]
INTERVIEW_TYPES: list[str] = ["technical", "hr", "mixed"]

ROLE_SYNONYMS: dict[str, list[str]] = {
    "backend": ["backend", "back-end", "back end", "бэкенд", "бекенд", "беккенд"],
    "frontend": ["frontend", "front-end", "front end", "фронтенд", "фронт"],
    "data_engineer": [
        "data engineer", "data-engineer", "data engineering",
        "дата инженер", "дата-инженер", "дата-инжинер", "дата инжинер",
    ],
    "ml_engineer": [
        "ml engineer", "ml-engineer", "machine learning",
        "ml инженер", "мл инженер", "мл-инженер", "машинное обучение",
    ],
    "qa": ["qa", "quality assurance", "тестировщик", "тестир"],
}

LEVEL_SYNONYMS: dict[str, list[str]] = {
    "junior": ["junior", "джуниор", "джун", "начинающ", "младш"],
    "middle": ["middle", "мидл", "средн"],
    "senior": ["senior", "сеньор", "синьор", "старш", "ведущ"],
}

TYPE_SYNONYMS: dict[str, list[str]] = {
    "technical": ["technical", "техн"],
    "hr": ["hr", "поведенч", "behavioral", "behaviour"],
    "mixed": ["mixed", "микс", "смеш", "комбин"],
}

MIN_ANSWER_LEN: int = 2
MAX_ANSWER_LEN: int = 8000


def _match(value: str, synonyms: dict[str, list[str]]) -> str | None:
    """Return the canonical key whose matching variant is most specific.

    Tie-break order:
    1. longest matching variant wins — prevents "ml" beating "machine learning";
    2. on equal length, the one that appears later in the text wins —
       handles negations like "не junior, а senior", where the user's true
       intent is the last bucket they named.
    """
    text = value.lower().strip()
    if not text:
        return None
    best_canonical: str | None = None
    best_length: int = 0
    best_position: int = -1
    for canonical, variants in synonyms.items():
        for variant in variants:
            position = text.rfind(variant)
            if position == -1:
                continue
            length = len(variant)
            longer = length > best_length
            same_length_later = length == best_length and position > best_position
            if longer or same_length_later:
                best_canonical = canonical
                best_length = length
                best_position = position
    return best_canonical


def validate_role(value: str) -> str | None:
    if not value:
        return None
    if value in ROLES:
        return value
    return _match(value, ROLE_SYNONYMS)


def validate_level(value: str) -> str | None:
    if not value:
        return None
    if value in LEVELS:
        return value
    return _match(value, LEVEL_SYNONYMS)


def validate_interview_type(value: str) -> str | None:
    if not value:
        return None
    if value in INTERVIEW_TYPES:
        return value
    return _match(value, TYPE_SYNONYMS)


def parse_context(text: str) -> dict[str, str | None]:
    return {
        "role": validate_role(text),
        "level": validate_level(text),
        "interview_type": validate_interview_type(text),
    }


def validate_answer(text: str) -> tuple[bool, str | None]:
    if text is None:
        return False, "Ответ не должен быть пустым."
    stripped = text.strip()
    if len(stripped) < MIN_ANSWER_LEN:
        return False, "Ответ слишком короткий — пожалуйста, раскройте мысль подробнее."
    if len(stripped) > MAX_ANSWER_LEN:
        return False, f"Ответ слишком длинный (>{MAX_ANSWER_LEN} символов)."
    return True, None

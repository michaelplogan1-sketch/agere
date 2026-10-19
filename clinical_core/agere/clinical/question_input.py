"""Bound the complete analytical request without dropping its definitions."""

MAX_QUESTION_CHARS = 12_000


def validate_question(question):
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f'Question must contain 1-{MAX_QUESTION_CHARS} characters')
    return question

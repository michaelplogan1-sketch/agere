"""Bound the complete analytical request without dropping its definitions."""

MAX_QUESTION_CHARS = 12_000


class QuestionNotUnderstood(ValueError):
    """No executable interpretation; this is not evidence that source data is absent."""


def failure(exc):
    status = 'not_understood' if isinstance(exc, QuestionNotUnderstood) else 'internal_error'
    return {'status': status, 'error': str(exc), 'data_absence_established': False}


def validate_question(question):
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f'Question must contain 1-{MAX_QUESTION_CHARS} characters')
    return question

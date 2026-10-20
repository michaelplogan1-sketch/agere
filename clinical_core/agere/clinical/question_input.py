"""Bound the complete analytical request without dropping its definitions."""

MAX_QUESTION_CHARS = 12_000


class QuestionNotUnderstood(ValueError):
    """No executable interpretation; this is not evidence that source data is absent."""
    def __init__(self, message, *, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def failure(exc):
    status = 'not_understood' if isinstance(exc, QuestionNotUnderstood) else 'internal_error'
    result = {'status': status, 'error': str(exc), 'data_absence_established': False}
    if hasattr(exc, 'planner_receipt'):
        from .shared_planner import economics
        result.update(new_paid_api_calls=exc.planner_receipt['new_paid_api_calls'],
                      planning_economics=economics(exc.planner_receipt))
    return result


def validate_question(question):
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f'Question must contain 1-{MAX_QUESTION_CHARS} characters')
    return question

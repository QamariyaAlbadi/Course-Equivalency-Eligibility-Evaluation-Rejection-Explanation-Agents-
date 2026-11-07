class AgentError(Exception):
    """Base class for all errors the agents raise deliberately (never a raw traceback to the user)."""


class DatabaseUnavailableError(AgentError):
    pass


class RequestNotFoundError(AgentError):
    pass


class NotCourseEquivalencyRequestError(AgentError):
    pass


class StudentNotFoundError(AgentError):
    pass


class CourseNotFoundError(AgentError):
    pass


class KnowledgeBaseError(AgentError):
    pass


class StorageError(AgentError):
    pass


class DocumentReadError(AgentError):
    pass


class GeminiUnavailableError(AgentError):
    """Raised internally when Gemini cannot be reached; callers fall back safely."""

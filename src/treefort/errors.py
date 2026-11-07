class WorktreeError(Exception):
    """Base exception for all treefort errors"""

    pass


# Validation errors - invalid user input
class ValidationError(WorktreeError):
    """Raised when user input is invalid or malformed"""

    pass


class WorktreeNameError(ValidationError):
    """Raised when worktree name is invalid"""

    pass


class BranchInputError(ValidationError):
    """Raised when branch/PR input cannot be parsed"""

    pass


class RepositoryPathError(ValidationError):
    """Raised when repository path/URL is invalid"""

    pass


class ConfigTypeError(ValidationError):
    """Raised when config type is invalid or mismatched"""

    pass


class RemoteUsageError(ValidationError):
    """Raised when remote is used incorrectly"""

    pass


# Not found errors - resources don't exist
class NotFoundError(WorktreeError):
    """Raised when a required resource is not found"""

    pass


class ProjectNotFoundError(NotFoundError):
    """Raised when project directory is not found"""

    pass


class WorktreeNotFoundError(NotFoundError):
    """Raised when worktree doesn't exist"""

    pass


class RemoteNotFoundError(NotFoundError):
    """Raised when git remote is not found"""

    pass


class AncestorNotFoundError(NotFoundError):
    """Raised when file/directory not found in ancestor paths"""

    pass


# Conflict errors - resource already exists
class ConflictError(WorktreeError):
    """Raised when resource already exists"""

    pass


# Hook errors
class HookError(WorktreeError):
    """Raised when hook execution fails"""

    pass


class HookExistsError(HookError, ConflictError):
    """Raised when hook already exists"""

    pass


class ProjectExistsError(ConflictError):
    """Raised when project directory already exists"""

    pass


class AliasConflictError(ConflictError):
    """Raised when there are conflicting aliases across subcommands"""

    pass


class TemplateExistsError(ConflictError):
    """Raised when template already exists"""

    pass


# Command errors - git/command execution failures
class CommandError(WorktreeError):
    """Raised when a git or shell command fails"""

    pass


# Completion template errors - upstream template drift
class CompletionTemplateError(WorktreeError):
    """Raised when fire's completion template no longer matches what shell-init patches"""

    pass

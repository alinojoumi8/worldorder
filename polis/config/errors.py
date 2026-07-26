class PolisError(Exception):
    """Base exception for expected POLIS failures."""


class ConfigError(PolisError):
    """Configuration cannot be loaded or validated."""


class ProfileNotFound(ConfigError):
    """A requested configuration profile does not exist."""


class MechanismError(ConfigError):
    """A mechanism registration is invalid."""


class RuntimeOverlayError(PolisError):
    """A runtime policy overlay violates temporal rules."""

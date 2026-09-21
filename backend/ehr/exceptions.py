class EHRIntegrationException(Exception):
    """Base exception for all external healthcare system integrations."""
    pass


class MockEhrTimeoutException(EHRIntegrationException):
    """Raised when Mock EHR simulated network or gateway timeout occurs."""
    def __init__(self, message: str = "Mock EHR HTTP 504 Gateway Timeout (Simulated)", saved_internally: bool = False):
        super().__init__(message)
        self.message = message
        self.saved_internally = saved_internally


class EHRPatientNotFoundException(EHRIntegrationException):
    """Raised when patient lookup fails in external healthcare system."""
    pass


class EHRProviderNotFoundException(EHRIntegrationException):
    """Raised when provider lookup fails in external healthcare system."""
    pass


class EHRSlotUnavailableException(EHRIntegrationException):
    """Raised when requested slot is unavailable in external healthcare system."""
    pass


class EHRNetworkException(EHRIntegrationException):
    """Raised on socket drop, network unreachable, or connection reset."""
    pass


class EHRAuthenticationException(EHRIntegrationException):
    """Raised on HTTP 401 or 403 authorization failure with external system."""
    pass


class EHRRateLimitException(EHRIntegrationException):
    """Raised on HTTP 429 Too Many Requests rate limiting."""
    pass


class EHROutageException(EHRIntegrationException):
    """Raised on HTTP 503 Service Unavailable or external system maintenance outage."""
    pass


class EHRValidationException(EHRIntegrationException):
    """Raised on HTTP 400 or 422 payload or schema mapping errors."""
    pass


class EHRUnknownOutcomeException(EHRIntegrationException):
    """Raised when external state cannot be determined or verified."""
    pass

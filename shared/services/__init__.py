"""Service exports are lazy so backend text routing imports no camera/audio runtime."""

__all__ = ['StateService']


def __getattr__(name):
    if name == 'StateService':
        from .state_service import StateService
        return StateService
    raise AttributeError(name)

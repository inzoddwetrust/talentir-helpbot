"""
Dependency injection container for services.
"""
import logging
from typing import Dict, Any, Type, TypeVar, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Global services registry
_services: Dict[str, Any] = {}


def register_service(service_class: Type[T], instance: T) -> None:
    """
    Register a service instance.

    Args:
        service_class: Service class
        instance: Service instance
    """
    service_name = service_class.__name__
    logger.debug(f"Registering service: {service_name}")
    _services[service_name] = instance


def get_service(service_class: Type[T]) -> Optional[T]:
    """
    Get a service instance by class.

    Args:
        service_class: Service class

    Returns:
        Service instance or None if not found
    """
    service_name = service_class.__name__
    service = _services.get(service_name)

    if not service:
        logger.warning(f"Service {service_name} not found in registry")

    return service


def get_all_services() -> Dict[str, Any]:
    """
    Get all registered services.

    Returns:
        Dict of service name -> instance
    """
    return _services.copy()
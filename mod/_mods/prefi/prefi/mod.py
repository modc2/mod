"""Professional Base Modification Framework.

This module provides a standardized base class for creating modular
extensions and customizations with a consistent, professional interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseMod(ABC):
    """Professional base modification template.
    
    This abstract base class serves as a foundational template for creating
    modular extensions and customizations. It provides a standardized interface
    for implementing modifications in a clean, maintainable, and scalable manner.
    
    Attributes:
        description (str): A comprehensive description of the modification's
            purpose, functionality, and intended use cases.
        version (str): Semantic version number of the modification.
        author (str): Author or maintainer of the modification.
    
    Example:
        Create a custom modification by inheriting from BaseMod:
        
        >>> class CustomMod(BaseMod):
        ...     description = "Custom data processing modification"
        ...     version = "1.0.0"
        ...     
        ...     def execute(self, **kwargs) -> Dict[str, Any]:
        ...         # Implementation logic
        ...         return {"status": "success", "data": processed_data}
        ...
        >>> mod = CustomMod()
        >>> result = mod.execute(input_data=data)
    
    Notes:
        - All subclasses must implement the execute() method
        - Follow semantic versioning for the version attribute
        - Provide clear, comprehensive documentation for all implementations
    """
    
    description: str = """
    Professional base modification template.
    
    This template provides a standardized foundation for building
    modular components with consistent structure, comprehensive
    documentation, and maintainable code practices.
    """
    
    version: str = "1.0.0"
    author: str = "Framework Team"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the base modification.
        
        Args:
            config: Optional configuration dictionary for the modification.
        """
        self.config = config or {}
        self._validate_configuration()
    
    def _validate_configuration(self) -> None:
        """Validate the modification configuration.
        
        Override this method to implement custom validation logic.
        
        Raises:
            ValueError: If configuration is invalid.
        """
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the modification logic.
        
        This method must be implemented by all subclasses to define
        the core functionality of the modification.
        
        Args:
            **kwargs: Arbitrary keyword arguments for modification execution.
        
        Returns:
            Dictionary containing execution results and status information.
        
        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError(
            "Subclasses must implement the execute() method"
        )
    
    def get_info(self) -> Dict[str, str]:
        """Retrieve modification metadata.
        
        Returns:
            Dictionary containing description, version, and author information.
        """
        return {
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "class_name": self.__class__.__name__
        }
    
    def __repr__(self) -> str:
        """Return string representation of the modification."""
        return f"{self.__class__.__name__}(version={self.version})"

"""
Query module - Unified interface for querying free AI models.

Automatically uses free models from OpenRouter or Venice AI.
"""

import mod as m


class Mod:
    """
    Query free AI models from OpenRouter or Venice.

    Provides a simple interface to query various free LLMs without managing API keys.
    """

    description = "Query free AI models from OpenRouter or Venice"

    fns = [
        'query',
        'list_free_models',
        'openrouter_query',
        'venice_query',
    ]

    def __init__(self, path='~/.mod/query', **kwargs):
        """Initialize the query module."""
        self.store = m.mod('store')(path)
        self.openrouter = None
        self.venice = None

    def _get_openrouter(self):
        """Lazy load OpenRouter instance."""
        if self.openrouter is None:
            self.openrouter = m.mod('openrouter')()
        return self.openrouter

    def _get_venice(self):
        """Lazy load Venice instance."""
        if self.venice is None:
            self.venice = m.mod('venice')()
        return self.venice

    def list_free_models(self, update=False, info=False):
        """
        List all available free models from OpenRouter.

        Args:
            update: Force refresh from API
            info: Return full model info instead of just IDs

        Returns:
            list: Model IDs or model info dicts
        """
        router = self._get_openrouter()
        return router.free_models(update=update, info=info)

    def openrouter_query(
        self,
        query: str,
        model: str = None,
        stream: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs
    ):
        """
        Query using OpenRouter free models.

        Args:
            query: The question or prompt
            model: Optional specific model (otherwise picks first free model)
            stream: Whether to stream the response
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            **kwargs: Additional arguments passed to forward()

        Returns:
            str or generator: Response text or stream generator
        """
        router = self._get_openrouter()

        # Get free models if no model specified
        if model is None:
            free_models = router.free_models()
            if not free_models:
                free_models = router.free_models(update=True)
            if not free_models:
                raise ValueError("No free models available on OpenRouter")
            model = free_models[0]
            print(f"Using free model: {model}")

        return router.forward(
            query,
            model=model,
            stream=stream,
            max_tokens=max_tokens,
            temperature=temperature,
            free=True,
            **kwargs
        )

    def venice_query(
        self,
        query: str,
        model: str = None,
        stream: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs
    ):
        """
        Query using Venice AI.

        Args:
            query: The question or prompt
            model: Optional specific model
            stream: Whether to stream the response
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            **kwargs: Additional arguments passed to forward()

        Returns:
            str or generator: Response text or stream generator
        """
        venice = self._get_venice()

        if model:
            print(f"Using Venice model: {model}")
        else:
            print(f"Using Venice default model: {venice.model}")

        return venice.forward(
            query,
            model=model,
            stream=stream,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs
        )

    def query(
        self,
        query: str,
        use_venice: bool = False,
        model: str = None,
        stream: bool = False,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs
    ):
        """
        Query a free AI model.

        Args:
            query: The question or prompt
            use_venice: If True, use Venice instead of OpenRouter
            model: Optional specific model to use
            stream: Whether to stream the response
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            **kwargs: Additional arguments

        Returns:
            str or generator: Response text or stream generator

        Examples:
            >>> q = m.mod('query')()
            >>> q.query("What is the capital of France?")
            'The capital of France is Paris.'

            >>> # Stream the response
            >>> for chunk in q.query("Tell me a story", stream=True):
            ...     print(chunk, end='')

            >>> # Use Venice
            >>> q.query("Explain quantum computing", use_venice=True)
        """
        if use_venice:
            return self.venice_query(
                query,
                model=model,
                stream=stream,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )
        else:
            return self.openrouter_query(
                query,
                model=model,
                stream=stream,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )

    def test(self):
        """Test the query module."""
        print("Testing OpenRouter query...")
        response = self.query("What is 2+2?", stream=False, max_tokens=100)
        print(f"Response: {response}")

        print("\nTesting streaming...")
        print("Response: ", end='')
        for chunk in self.query("Say hello", stream=True, max_tokens=50):
            print(chunk, end='')
        print()

        return {'status': 'success'}

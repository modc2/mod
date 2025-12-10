class BaseMod:
    description = """
    ChatGPT Clone - Full-stack chat application with memory
    
    A complete chat interface that mimics ChatGPT with conversation memory,
    built with FastAPI backend and vanilla JavaScript frontend.
    
    Features:
    - ChatGPT-like UI with dark theme
    - Conversation memory across messages
    - Multiple conversation support
    - RESTful API with FastAPI
    - Docker containerization
    - Real-time message streaming
    - Conversation history management
    
    Quick Start:
        docker-compose up --build
        
    Access:
        Frontend: http://localhost:3000
        API: http://localhost:8000
        API Docs: http://localhost:8000/docs
    
    Architecture:
        - chat.api: FastAPI backend with in-memory conversation storage
        - chat.app: Single-page application with ChatGPT-inspired UI
        - Docker: Containerized deployment with docker-compose
    
    Tech Stack:
        Backend: FastAPI, Pydantic, Uvicorn
        Frontend: HTML5, CSS3, Vanilla JavaScript
        DevOps: Docker, Docker Compose, Nginx
    """
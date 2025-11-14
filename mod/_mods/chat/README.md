# ChatGPT Clone - Full Stack Chat Application

A complete chat interface that mimics ChatGPT with conversation memory, built with FastAPI backend and vanilla JavaScript frontend.

## Features

- 🎨 ChatGPT-like UI with dark theme
- 🧠 Conversation memory across messages
- 💬 Multiple conversation support
- 🚀 RESTful API with FastAPI
- 🐳 Docker containerization
- 📝 Conversation history management
- ⚡ Real-time message updates

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Build and start all services
docker-compose up --build

# Run in detached mode
docker-compose up -d --build

# Stop services
docker-compose down
```

### Access the Application

- **Frontend**: http://localhost:3000
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

## Architecture

```
chat/
├── chat.api/              # FastAPI Backend
│   ├── main.py           # API endpoints and logic
│   └── Dockerfile        # API container config
├── chat.app/              # Frontend Application
│   ├── index.html        # Single-page application
│   └── Dockerfile        # App container config
├── docker-compose.yml     # Multi-container orchestration
└── requirements.txt       # Python dependencies
```

## API Endpoints

### Chat
- `POST /api/chat` - Send a message and get response
- `POST /api/conversations/new` - Create new conversation

### Conversations
- `GET /api/conversations` - List all conversations
- `GET /api/conversations/{id}` - Get specific conversation
- `DELETE /api/conversations/{id}` - Delete conversation

## Tech Stack

### Backend
- **FastAPI** - Modern Python web framework
- **Pydantic** - Data validation
- **Uvicorn** - ASGI server

### Frontend
- **HTML5/CSS3** - Modern web standards
- **Vanilla JavaScript** - No framework dependencies
- **Fetch API** - HTTP requests

### DevOps
- **Docker** - Containerization
- **Docker Compose** - Multi-container orchestration
- **Nginx** - Web server for frontend

## Development

### Running Locally Without Docker

#### Backend
```bash
cd chat.api
pip install -r ../requirements.txt
uvicorn main:app --reload --port 8000
```

#### Frontend
```bash
cd chat.app
python -m http.server 3000
```

## Memory System

The application uses in-memory storage for conversations:
- Each conversation has a unique ID
- All messages are stored with timestamps
- Conversations persist during container lifetime
- Full conversation history is maintained

## Customization

### Change Ports

Edit `docker-compose.yml`:
```yaml
ports:
  - "YOUR_PORT:80"  # Frontend
  - "YOUR_PORT:8000" # Backend
```

### Modify AI Response

Edit `chat.api/main.py` in the `chat()` function to customize response logic.

### Style Changes

Edit the `<style>` section in `chat.app/index.html`.

## Production Considerations

- Add persistent storage (PostgreSQL, MongoDB)
- Implement authentication
- Add rate limiting
- Use environment variables for configuration
- Add HTTPS/SSL certificates
- Implement proper error handling
- Add logging and monitoring

## License

MIT License - Feel free to use and modify!

## Author

Built with ❤️ by Mr. Robot, Leonardo da Vinci, and the spirit of innovation!

# Simple CRUD API

A basic REST API built with FastAPI demonstrating Create, Read, Update, Delete operations for managing items.

## Features

- ✅ Create new items
- ✅ Read all items or a specific item
- ✅ Update existing items
- ✅ Delete items
- ✅ In-memory storage (simple, no database required)
- ✅ Automatic API documentation (Swagger UI)
- ✅ Type validation with Pydantic

## Installation

```bash
# Install dependencies
pip install fastapi uvicorn pydantic
```

## Running the API

```bash
# Run the server
python crud_api.py

# Or use uvicorn directly
uvicorn crud_api:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## API Endpoints

### Root
```
GET /
```
Returns API information and available endpoints.

### Create Item
```
POST /items
Content-Type: application/json

{
  "name": "Laptop",
  "description": "High-performance laptop",
  "price": 999.99,
  "quantity": 10
}
```

### Get All Items
```
GET /items
```

### Get Single Item
```
GET /items/{item_id}
```

### Update Item
```
PUT /items/{item_id}
Content-Type: application/json

{
  "name": "Updated Laptop",
  "description": "Updated description",
  "price": 899.99,
  "quantity": 15
}
```

### Delete Item
```
DELETE /items/{item_id}
```

## Example Usage with cURL

### Create an item
```bash
curl -X POST "http://localhost:8000/items" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Laptop",
    "description": "High-performance laptop",
    "price": 999.99,
    "quantity": 10
  }'
```

### Get all items
```bash
curl "http://localhost:8000/items"
```

### Get a specific item
```bash
curl "http://localhost:8000/items/1"
```

### Update an item
```bash
curl -X PUT "http://localhost:8000/items/1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Laptop",
    "description": "Updated description",
    "price": 899.99,
    "quantity": 15
  }'
```

### Delete an item
```bash
curl -X DELETE "http://localhost:8000/items/1"
```

## Example Usage with Python

```python
import requests

BASE_URL = "http://localhost:8000"

# Create an item
response = requests.post(f"{BASE_URL}/items", json={
    "name": "Laptop",
    "description": "High-performance laptop",
    "price": 999.99,
    "quantity": 10
})
item = response.json()
print(f"Created item: {item}")

# Get all items
response = requests.get(f"{BASE_URL}/items")
items = response.json()
print(f"All items: {items}")

# Get specific item
item_id = item["id"]
response = requests.get(f"{BASE_URL}/items/{item_id}")
print(f"Item {item_id}: {response.json()}")

# Update item
response = requests.put(f"{BASE_URL}/items/{item_id}", json={
    "name": "Updated Laptop",
    "description": "Updated description",
    "price": 899.99,
    "quantity": 15
})
print(f"Updated item: {response.json()}")

# Delete item
response = requests.delete(f"{BASE_URL}/items/{item_id}")
print(f"Delete response: {response.json()}")
```

## Data Model

### Item (Request)
```json
{
  "name": "string",
  "description": "string (optional)",
  "price": "float",
  "quantity": "integer (default: 0)"
}
```

### ItemResponse
```json
{
  "id": "integer",
  "name": "string",
  "description": "string",
  "price": "float",
  "quantity": "integer",
  "created_at": "ISO 8601 timestamp",
  "updated_at": "ISO 8601 timestamp"
}
```

## Notes

- This API uses in-memory storage, so all data is lost when the server restarts
- For production use, consider adding:
  - Database integration (PostgreSQL, MongoDB, etc.)
  - Authentication and authorization
  - Rate limiting
  - Pagination for list endpoints
  - Input validation and sanitization
  - Logging and monitoring
  - Error handling improvements

## License

MIT

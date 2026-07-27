"""Simple CRUD API using FastAPI

A basic REST API demonstrating Create, Read, Update, Delete operations
for managing items with in-memory storage.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime

app = FastAPI(title="Simple CRUD API", version="1.0.0")

# In-memory storage
items_db: Dict[int, dict] = {}
next_id = 1


class Item(BaseModel):
    """Item model for request/response"""
    name: str
    description: Optional[str] = None
    price: float
    quantity: int = 0


class ItemResponse(Item):
    """Item response model with ID and timestamp"""
    id: int
    created_at: str
    updated_at: str


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "Simple CRUD API",
        "endpoints": {
            "create": "POST /items",
            "read_all": "GET /items",
            "read_one": "GET /items/{item_id}",
            "update": "PUT /items/{item_id}",
            "delete": "DELETE /items/{item_id}"
        }
    }


@app.post("/items", response_model=ItemResponse, status_code=201)
def create_item(item: Item):
    """Create a new item"""
    global next_id
    
    item_id = next_id
    next_id += 1
    
    timestamp = datetime.utcnow().isoformat()
    item_data = {
        "id": item_id,
        **item.dict(),
        "created_at": timestamp,
        "updated_at": timestamp
    }
    
    items_db[item_id] = item_data
    return item_data


@app.get("/items", response_model=list[ItemResponse])
def read_items():
    """Get all items"""
    return list(items_db.values())


@app.get("/items/{item_id}", response_model=ItemResponse)
def read_item(item_id: int):
    """Get a specific item by ID"""
    if item_id not in items_db:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return items_db[item_id]


@app.put("/items/{item_id}", response_model=ItemResponse)
def update_item(item_id: int, item: Item):
    """Update an existing item"""
    if item_id not in items_db:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    
    existing_item = items_db[item_id]
    updated_data = {
        "id": item_id,
        **item.dict(),
        "created_at": existing_item["created_at"],
        "updated_at": datetime.utcnow().isoformat()
    }
    
    items_db[item_id] = updated_data
    return updated_data


@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    """Delete an item"""
    if item_id not in items_db:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    
    deleted_item = items_db.pop(item_id)
    return {"message": f"Item {item_id} deleted successfully", "item": deleted_item}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

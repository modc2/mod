"""Tests for the Simple CRUD API"""

import pytest
from fastapi.testclient import TestClient
from crud_api import app, items_db, next_id

client = TestClient(app)


def setup_function():
    """Clear the database before each test"""
    items_db.clear()
    globals()['next_id'] = 1


def test_root():
    """Test root endpoint"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "endpoints" in data


def test_create_item():
    """Test creating a new item"""
    item_data = {
        "name": "Test Laptop",
        "description": "A test laptop",
        "price": 999.99,
        "quantity": 5
    }
    response = client.post("/items", json=item_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Laptop"
    assert data["price"] == 999.99
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_read_items_empty():
    """Test reading items when database is empty"""
    response = client.get("/items")
    assert response.status_code == 200
    assert response.json() == []


def test_read_items():
    """Test reading all items"""
    # Create two items
    client.post("/items", json={"name": "Item 1", "price": 10.0})
    client.post("/items", json={"name": "Item 2", "price": 20.0})
    
    response = client.get("/items")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Item 1"
    assert data[1]["name"] == "Item 2"


def test_read_item():
    """Test reading a specific item"""
    # Create an item
    create_response = client.post("/items", json={
        "name": "Test Item",
        "price": 50.0
    })
    item_id = create_response.json()["id"]
    
    # Read the item
    response = client.get(f"/items/{item_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == item_id
    assert data["name"] == "Test Item"


def test_read_item_not_found():
    """Test reading a non-existent item"""
    response = client.get("/items/999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_update_item():
    """Test updating an item"""
    # Create an item
    create_response = client.post("/items", json={
        "name": "Original Name",
        "price": 100.0,
        "quantity": 10
    })
    item_id = create_response.json()["id"]
    
    # Update the item
    update_data = {
        "name": "Updated Name",
        "price": 150.0,
        "quantity": 15
    }
    response = client.put(f"/items/{item_id}", json=update_data)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["price"] == 150.0
    assert data["quantity"] == 15
    assert data["created_at"] != data["updated_at"]


def test_update_item_not_found():
    """Test updating a non-existent item"""
    response = client.put("/items/999", json={
        "name": "Test",
        "price": 10.0
    })
    assert response.status_code == 404


def test_delete_item():
    """Test deleting an item"""
    # Create an item
    create_response = client.post("/items", json={
        "name": "To Delete",
        "price": 25.0
    })
    item_id = create_response.json()["id"]
    
    # Delete the item
    response = client.delete(f"/items/{item_id}")
    assert response.status_code == 200
    data = response.json()
    assert "deleted successfully" in data["message"].lower()
    
    # Verify it's gone
    get_response = client.get(f"/items/{item_id}")
    assert get_response.status_code == 404


def test_delete_item_not_found():
    """Test deleting a non-existent item"""
    response = client.delete("/items/999")
    assert response.status_code == 404


def test_full_crud_workflow():
    """Test complete CRUD workflow"""
    # Create
    create_response = client.post("/items", json={
        "name": "Workflow Item",
        "description": "Testing full workflow",
        "price": 75.0,
        "quantity": 20
    })
    assert create_response.status_code == 201
    item_id = create_response.json()["id"]
    
    # Read
    read_response = client.get(f"/items/{item_id}")
    assert read_response.status_code == 200
    assert read_response.json()["name"] == "Workflow Item"
    
    # Update
    update_response = client.put(f"/items/{item_id}", json={
        "name": "Updated Workflow Item",
        "description": "Updated description",
        "price": 85.0,
        "quantity": 25
    })
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated Workflow Item"
    
    # Delete
    delete_response = client.delete(f"/items/{item_id}")
    assert delete_response.status_code == 200
    
    # Verify deletion
    final_response = client.get(f"/items/{item_id}")
    assert final_response.status_code == 404

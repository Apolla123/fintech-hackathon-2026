from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
import json
import os

app = FastAPI()


# Загрузка данных
def load_data():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            content = f.read()
            if content.strip() == "":
                return []
            return json.loads(content)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


# Сохранение данных
def save_data():
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(requests, f, ensure_ascii=False, indent=2)


requests = load_data()


# Модель заявки
class RequestModel(BaseModel):
    title: str
    description: str
    creator: str


# Модель для изменения статуса
class StatusModel(BaseModel):
    status: str


@app.get("/", response_class=HTMLResponse)
def get_index():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "templates", "index.html")

    if not os.path.exists(file_path):
        return {"error": "Файл templates/index.html не найден"}

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# Получить все заявки
@app.get("/requests")
def get_requests():
    return requests


# Создать заявку
@app.post("/requests")
def create_request(request: RequestModel):
    new_request = {
        "id": len(requests) + 1,
        "title": request.title,
        "description": request.description,
        "creator": request.creator,
        "status": "new",
        "assigned_to": None,
        "created_at": datetime.now().isoformat()
    }
    requests.append(new_request)
    save_data()
    return new_request


# Назначить ответственного
@app.post("/requests/{request_id}/assign")
def assign_request(request_id: int, assigned_to: str = Query(...)):
    for req in requests:
        if req["id"] == request_id:
            req["assigned_to"] = assigned_to
            save_data()
            return req
    return {"error": "Заявка не найдена"}


# Изменить статус
@app.put("/requests/{request_id}/status")
def update_status(request_id: int, status_data: StatusModel):
    for req in requests:
        if req["id"] == request_id:
            req["status"] = status_data.status
            save_data()
            return req
    return {"error": "Заявка не найдена"}


# Аналитика
@app.get("/analytics")
def get_analytics():
    total = len(requests)
    new = sum(1 for r in requests if r["status"] == "new")
    in_progress = sum(1 for r in requests if r["status"] == "in_progress")
    done = sum(1 for r in requests if r["status"] == "done")
    return {
        "total": total,
        "new": new,
        "in_progress": in_progress,
        "done": done
    }


# uvicorn main:app --reload
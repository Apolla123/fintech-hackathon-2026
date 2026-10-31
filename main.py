from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
from urllib.parse import unquote
from typing import Optional
import json
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")        # активные заявки
ARCHIVE_FILE = os.path.join(BASE_DIR, "archive.json")  # выполненные заявки (архив)
USERS_FILE = os.path.join(BASE_DIR, "users.json")      # кто когда-либо заходил на сайт

# Роли, которые бывают на сайте
ROLES = {
    "guest": "Гость",
    "employee": "Сотрудник",
    "manager": "Руководитель",
}

STATUSES = ["new", "in_progress", "done"]


# ---------------------------------------------------------------------------
# Работа с JSON-файлами
# ---------------------------------------------------------------------------

def load_json(path):
    """Читает список из JSON-файла. Если файла нет или он пустой/битый — пустой список."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if content.strip() == "":
                return []
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_data():
    save_json(DATA_FILE, requests)


def save_archive():
    save_json(ARCHIVE_FILE, archive)


requests = load_json(DATA_FILE)
archive = load_json(ARCHIVE_FILE)
users = load_json(USERS_FILE)

# При запуске переносим уже выполненные заявки (status = done) в архив,
# чтобы в списке активных остались только невыполненные.
_done = [r for r in requests if r.get("status") == "done"]
if _done:
    for r in _done:
        r.setdefault("completed_at", datetime.now().isoformat())
        archive.append(r)
    requests = [r for r in requests if r.get("status") != "done"]
    save_data()
    save_archive()


def next_id():
    """Новый id = самый большой id среди активных и архивных + 1 (чтобы id не повторялись)."""
    all_ids = [r["id"] for r in requests] + [r["id"] for r in archive]
    return max(all_ids, default=0) + 1


def find_request(request_id):
    for req in requests:
        if req["id"] == request_id:
            return req
    raise HTTPException(status_code=404, detail="Заявка не найдена")


# ---------------------------------------------------------------------------
# "Авторизация" без пароля.
# Браузер после входа запоминает имя и роль и присылает их в каждом запросе
# в заголовках X-User-Name и X-User-Role. Сервер по ним решает, что можно.
# ---------------------------------------------------------------------------

def get_user(x_user_name: Optional[str], x_user_role: Optional[str]):
    if not x_user_name or not x_user_role:
        raise HTTPException(status_code=401, detail="Сначала войдите на сайт")
    name = unquote(x_user_name).strip()  # имя приходит закодированным (из-за русских букв)
    role = x_user_role.strip()
    if not name or role not in ROLES:
        raise HTTPException(status_code=401, detail="Неизвестный пользователь или роль")
    return {"name": name, "role": role}


def require_role(user, *allowed):
    if user["role"] not in allowed:
        raise HTTPException(status_code=403, detail="У вашей роли нет доступа к этому действию")


def visible_for(user, req):
    """Может ли пользователь видеть эту заявку."""
    if user["role"] == "manager":
        return True  # руководитель видит всё
    if user["role"] == "employee":
        # сотрудник видит только свободные заявки и свои
        return req.get("assigned_to") in (None, "") or req.get("assigned_to") == user["name"]
    # гость видит только заявки, которые создал сам
    return req.get("creator") == user["name"]


# ---------------------------------------------------------------------------
# Модели (что присылает браузер)
# ---------------------------------------------------------------------------

class LoginModel(BaseModel):
    name: str
    role: str


class RequestModel(BaseModel):
    title: str
    description: str


class StatusModel(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Страница
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def get_index():
    file_path = os.path.join(BASE_DIR, "templates", "index.html")
    if not os.path.exists(file_path):
        return HTMLResponse("Файл templates/index.html не найден", status_code=500)
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------

@app.post("/login")
def login(data: LoginModel):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Введите имя")
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Выберите роль: гость, сотрудник или руководитель")

    # запоминаем пользователя (нужно, чтобы руководитель видел список сотрудников)
    existing = next((u for u in users if u["name"] == name and u["role"] == data.role), None)
    if existing:
        existing["last_login"] = datetime.now().isoformat()
    else:
        users.append({"name": name, "role": data.role, "last_login": datetime.now().isoformat()})
    save_json(USERS_FILE, users)

    return {"name": name, "role": data.role, "role_title": ROLES[data.role]}


# Список сотрудников — для выпадающего списка «Назначить» у руководителя
@app.get("/employees")
def get_employees(x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    require_role(user, "manager")
    names = {u["name"] for u in users if u["role"] == "employee"}
    # плюс все, кому уже когда-то назначали заявки
    names |= {r["assigned_to"] for r in requests + archive if r.get("assigned_to")}
    return sorted(names)


# ---------------------------------------------------------------------------
# Заявки
# ---------------------------------------------------------------------------

@app.get("/requests")
def get_requests(x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    return [r for r in requests if visible_for(user, r)]


@app.post("/requests")
def create_request(request: RequestModel, x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)  # создавать может любая роль
    if not request.title.strip():
        raise HTTPException(status_code=400, detail="Введите название заявки")
    new_request = {
        "id": next_id(),
        "title": request.title.strip(),
        "description": request.description.strip(),
        "creator": user["name"],
        "status": "new",
        "assigned_to": None,
        "created_at": datetime.now().isoformat(),
    }
    requests.append(new_request)
    save_data()
    return new_request


# Сотрудник берёт свободную заявку себе
@app.post("/requests/{request_id}/take")
def take_request(request_id: int, x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    require_role(user, "employee")
    req = find_request(request_id)
    if req.get("assigned_to"):
        raise HTTPException(status_code=409, detail="Эту заявку уже кто-то взял")
    req["assigned_to"] = user["name"]
    if req["status"] == "new":
        req["status"] = "in_progress"
    save_data()
    return req


# Отказаться от заявки (сотрудник — от своей, руководитель — от любой)
@app.post("/requests/{request_id}/release")
def release_request(request_id: int, x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    require_role(user, "employee", "manager")
    req = find_request(request_id)
    if user["role"] == "employee" and req.get("assigned_to") != user["name"]:
        raise HTTPException(status_code=403, detail="Можно отказаться только от своей заявки")
    req["assigned_to"] = None
    req["status"] = "new"
    save_data()
    return req


# Руководитель назначает ответственного
@app.post("/requests/{request_id}/assign")
def assign_request(request_id: int, assigned_to: str = Query(...),
                   x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    require_role(user, "manager")
    if not assigned_to.strip():
        raise HTTPException(status_code=400, detail="Укажите, кого назначить")
    req = find_request(request_id)
    req["assigned_to"] = assigned_to.strip()
    if req["status"] == "new":
        req["status"] = "in_progress"
    save_data()
    return req


# Изменить статус. Если статус стал «done» — заявка уезжает в архив.
@app.put("/requests/{request_id}/status")
def update_status(request_id: int, status_data: StatusModel,
                  x_user_name: str = Header(None), x_user_role: str = Header(None)):
    global requests
    user = get_user(x_user_name, x_user_role)
    require_role(user, "employee", "manager")
    if status_data.status not in STATUSES:
        raise HTTPException(status_code=400, detail="Неизвестный статус")
    req = find_request(request_id)
    if user["role"] == "employee" and req.get("assigned_to") != user["name"]:
        raise HTTPException(status_code=403, detail="Менять статус можно только у своих заявок")

    req["status"] = status_data.status
    if req["status"] == "done":
        req["completed_at"] = datetime.now().isoformat()
        archive.append(req)
        requests = [r for r in requests if r["id"] != request_id]
        save_archive()
    save_data()
    return req


# ---------------------------------------------------------------------------
# Архив
# ---------------------------------------------------------------------------

@app.get("/archive")
def get_archive(x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    if user["role"] == "manager":
        return archive
    if user["role"] == "employee":
        return [r for r in archive if r.get("assigned_to") == user["name"]]
    return [r for r in archive if r.get("creator") == user["name"]]


# Вернуть заявку из архива (только руководитель)
@app.post("/archive/{request_id}/restore")
def restore_request(request_id: int, x_user_name: str = Header(None), x_user_role: str = Header(None)):
    global archive
    user = get_user(x_user_name, x_user_role)
    require_role(user, "manager")
    req = next((r for r in archive if r["id"] == request_id), None)
    if req is None:
        raise HTTPException(status_code=404, detail="Заявка в архиве не найдена")
    archive = [r for r in archive if r["id"] != request_id]
    req.pop("completed_at", None)
    req["status"] = "in_progress" if req.get("assigned_to") else "new"
    req["restored_at"] = datetime.now().isoformat()
    requests.append(req)
    save_archive()
    save_data()
    return req


# ---------------------------------------------------------------------------
# Статистика (только руководитель)
# ---------------------------------------------------------------------------

@app.get("/analytics")
def get_analytics(x_user_name: str = Header(None), x_user_role: str = Header(None)):
    user = get_user(x_user_name, x_user_role)
    require_role(user, "manager")

    # статистика по каждому сотруднику
    by_employee = {}
    for r in requests + archive:
        who = r.get("assigned_to")
        if not who:
            continue
        stats = by_employee.setdefault(who, {"name": who, "in_progress": 0, "new": 0, "done": 0})
        stats[r["status"]] = stats.get(r["status"], 0) + 1

    return {
        "total": len(requests) + len(archive),
        "active": len(requests),
        "new": sum(1 for r in requests if r["status"] == "new"),
        "in_progress": sum(1 for r in requests if r["status"] == "in_progress"),
        "done": len(archive),
        "unassigned": sum(1 for r in requests if not r.get("assigned_to")),
        "by_employee": sorted(by_employee.values(), key=lambda s: -(s["in_progress"] + s["new"] + s["done"])),
    }


# Запуск: uvicorn main:app --reload

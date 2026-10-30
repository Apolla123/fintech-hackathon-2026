#!/usr/bin/env python3
"""Рабочая сторона системы заявок (сотрудники и руководитель).

Запуск:        python app.py          ->  http://localhost:8000
С тестовыми    python app.py --demo   (заполнит пустой список примерами)
данными:
Данные:        data/requests.json (активные), data/archive.json (архив)

Заявки от клиентов приходят с их стороны сайта через
POST /api/requests  {"title": ..., "client": ..., "description": ...}
"""
import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATIC = os.path.join(BASE, "static")
REQ_FILE = os.path.join(DATA, "requests.json")
ARC_FILE = os.path.join(DATA, "archive.json")

# Список сотрудников — поменяйте на своих
EMPLOYEES = ["Иванов И.", "Петрова А.", "Сидоров К."]
PORT = int(os.environ.get("PORT", 8000))

lock = threading.Lock()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path):
    """Читает список из JSON. Пустой или повреждённый файл не роняет программу:
    повреждённый сохраняется рядом как *.broken-ДАТА, чтобы данные не пропали."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return []
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except (ValueError, OSError):
        pass
    backup = path + ".broken-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        os.replace(path, backup)
        print(f"Файл {os.path.basename(path)} повреждён, сохранён как {os.path.basename(backup)}")
    except OSError:
        pass
    return []


def save(path, data):
    os.makedirs(DATA, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # атомарная запись — файл не повредится при сбое


def find(items, rid):
    for i, r in enumerate(items):
        if isinstance(r, dict) and r.get("id") == rid:
            return i
    return -1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 1_000_000:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/requests":
            with lock:
                return self.send_json(load(REQ_FILE))
        if path == "/api/archive":
            with lock:
                return self.send_json(load(ARC_FILE))
        if path == "/api/config":
            return self.send_json({"employees": EMPLOYEES})
        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?")[0]
        data = self.read_body()

        if path == "/api/requests":  # создать заявку
            title = str(data.get("title") or "").strip()
            client = str(data.get("client") or "").strip()
            if not title or not client:
                return self.send_json({"error": "Нужны название и имя клиента"}, 400)
            req = {
                "id": uuid.uuid4().hex[:8],
                "title": title[:200],
                "description": str(data.get("description") or "").strip()[:2000],
                "client": client[:100],
                "assignee": None,
                "created_at": now_iso(),
            }
            with lock:
                items = load(REQ_FILE)
                items.append(req)
                save(REQ_FILE, items)
            return self.send_json(req, 201)

        m = re.fullmatch(r"/api/requests/(\w+)/assign", path)
        if m:  # назначить / снять исполнителя
            assignee = data.get("assignee") or None
            if assignee is not None and assignee not in EMPLOYEES:
                return self.send_json({"error": "Неизвестный сотрудник"}, 400)
            with lock:
                items = load(REQ_FILE)
                i = find(items, m.group(1))
                if i < 0:
                    return self.send_json({"error": "Заявка не найдена"}, 404)
                items[i]["assignee"] = assignee
                save(REQ_FILE, items)
                return self.send_json(items[i])

        m = re.fullmatch(r"/api/requests/(\w+)/complete", path)
        if m:  # выполнена -> в архив
            with lock:
                items = load(REQ_FILE)
                i = find(items, m.group(1))
                if i < 0:
                    return self.send_json({"error": "Заявка не найдена"}, 404)
                req = items.pop(i)
                req["completed_at"] = now_iso()
                archive = load(ARC_FILE)
                archive.append(req)
                # сначала архив, потом список — заявка не потеряется при сбое
                save(ARC_FILE, archive)
                save(REQ_FILE, items)
                return self.send_json(req)

        m = re.fullmatch(r"/api/archive/(\w+)/restore", path)
        if m:  # вернуть из архива
            with lock:
                archive = load(ARC_FILE)
                i = find(archive, m.group(1))
                if i < 0:
                    return self.send_json({"error": "Заявки нет в архиве"}, 404)
                req = archive.pop(i)
                req.pop("completed_at", None)
                req.pop("completed_by", None)
                req["restored_at"] = now_iso()
                items = load(REQ_FILE)
                items.append(req)
                save(REQ_FILE, items)
                save(ARC_FILE, archive)
                return self.send_json(req)

        self.send_json({"error": "not found"}, 404)


def seed_demo():
    """Тестовые заявки с разными датами и исполнителями — для проверки сортировки."""
    now = datetime.now().astimezone()
    samples = [  # (название, клиент, исполнитель, часов назад)
        ("Не работает принтер", "ООО Ромашка", None, 52),
        ("Настроить почту", "ИП Орлов", "Иванов И.", 30),
        ("Замена картриджа", "ООО Ромашка", None, 5),
        ("Установить 1С", "Бета Групп", "Петрова А.", 75),
        ("Нет доступа к VPN", "Альфа", None, 1),
        ("Проверить сеть в офисе", "Бета Групп", "Иванов И.", 12),
        ("Новый сотрудник: учётка", "Альфа", "Сидоров К.", 100),
    ]
    items = [{
        "id": uuid.uuid4().hex[:8], "title": t, "description": "", "client": c,
        "assignee": a, "created_at": (now - timedelta(hours=h)).isoformat(timespec="seconds"),
    } for t, c, a, h in samples]
    save(REQ_FILE, items)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    for p in (REQ_FILE, ARC_FILE):
        if not os.path.exists(p):
            save(p, [])
    if "--demo" in sys.argv and not load(REQ_FILE):
        seed_demo()
        print("Добавлены тестовые заявки")
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        sys.exit(f"Не удалось запустить на порту {PORT}: {e}\n"
                 f"Порт занят? Запустите на другом: PORT=8080 python app.py")
    print(f"Открой в браузере: http://localhost:{PORT}  (остановка: Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")

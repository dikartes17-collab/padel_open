"""Падель-турнир: веб-приложение для организатора (открывается с телефона).

Запуск:  python app.py
Затем откройте в браузере телефона адрес http://<IP-компьютера>:8000
(компьютер и телефон должны быть в одной Wi-Fi-сети).
"""

from __future__ import annotations

import os
import socket
import sqlite3

from flask import Flask, jsonify, request, send_from_directory

import draw_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "padel.db")
POINTS_PER_GAME = 21

app = Flask(__name__, static_folder="static", static_url_path="")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                rating REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rnd INTEGER NOT NULL,
                seat INTEGER NOT NULL,
                a1 INTEGER NOT NULL,
                a2 INTEGER NOT NULL,
                b1 INTEGER NOT NULL,
                b2 INTEGER NOT NULL,
                score_a INTEGER
            );
            """
        )


def get_players(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM players ORDER BY id")]


def compute_standings(conn, players: list[dict]) -> list[dict]:
    info = {
        p["id"]: {"points": 0, "games": 0, "wins": 0, "losses": 0}
        for p in players
    }
    rows = conn.execute("SELECT * FROM games WHERE score_a IS NOT NULL")
    for g in rows:
        a_pts, b_pts = g["score_a"], POINTS_PER_GAME - g["score_a"]
        a_won = a_pts >= 11
        for pid, pts in ((g["a1"], a_pts), (g["a2"], a_pts), (g["b1"], b_pts), (g["b2"], b_pts)):
            rec = info[pid]
            rec["points"] += pts
            rec["games"] += 1
            if (pid in (g["a1"], g["a2"])) == a_won:
                rec["wins"] += 1
            else:
                rec["losses"] += 1
    standings = []
    for p in players:
        rec = info[p["id"]]
        standings.append({
            "id": p["id"],
            "name": f"{p['first_name']} {p['last_name']}",
            "rating": p["rating"],
            **rec,
        })
    standings.sort(key=lambda r: (-r["points"], -r["wins"], -r["rating"], r["name"]))
    return standings


def state_payload(conn) -> dict:
    players = get_players(conn)
    by_id = {p["id"]: p for p in players}

    def player_short(pid: int) -> dict:
        p = by_id[pid]
        return {"id": pid, "name": f"{p['first_name']} {p['last_name']}", "rating": p["rating"]}

    rounds = []
    rows = conn.execute("SELECT * FROM games ORDER BY rnd, seat").fetchall()
    for g in rows:
        rounds.append({
            "id": g["id"],
            "round": g["rnd"],
            "a": [player_short(g["a1"]), player_short(g["a2"])],
            "b": [player_short(g["b1"]), player_short(g["b2"])],
            "score_a": g["score_a"],
        })

    return {
        "players": [
            {"id": p["id"], "first_name": p["first_name"], "last_name": p["last_name"], "rating": p["rating"]}
            for p in players
        ],
        "draw": {"games": rounds} if rounds else None,
        "standings": compute_standings(conn, players),
        "supported_counts": draw_engine.supported_counts(),
        "points_per_game": POINTS_PER_GAME,
    }


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    with db() as conn:
        return jsonify(state_payload(conn))


@app.post("/api/players")
def api_add_player():
    data = request.get_json(silent=True) or {}
    first = str(data.get("first_name", "")).strip()
    last = str(data.get("last_name", "")).strip()
    try:
        rating = float(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите рейтинг числом, например 4.2"}), 400
    if not first or not last:
        return jsonify({"error": "Имя и фамилия обязательны"}), 400
    if not 0 <= rating <= 10:
        return jsonify({"error": "Рейтинг должен быть от 0 до 10"}), 400
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO players (first_name, last_name, rating) VALUES (?, ?, ?)",
            (first, last, rating),
        )
        conn.execute("DELETE FROM games")  # состав изменился - сетка недействительна
        return jsonify({"id": cur.lastrowid, **state_payload(conn)})


@app.put("/api/players/<int:pid>")
def api_edit_player(pid: int):
    data = request.get_json(silent=True) or {}
    first = str(data.get("first_name", "")).strip()
    last = str(data.get("last_name", "")).strip()
    try:
        rating = float(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите рейтинг числом, например 4.2"}), 400
    if not first or not last:
        return jsonify({"error": "Имя и фамилия обязательны"}), 400
    if not 0 <= rating <= 10:
        return jsonify({"error": "Рейтинг должен быть от 0 до 10"}), 400
    with db() as conn:
        cur = conn.execute(
            "UPDATE players SET first_name = ?, last_name = ?, rating = ? WHERE id = ?",
            (first, last, rating, pid),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Участник не найден"}), 404
        conn.execute("DELETE FROM games")
        return jsonify(state_payload(conn))


@app.delete("/api/players/<int:pid>")
def api_delete_player(pid: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM players WHERE id = ?", (pid,))
        if cur.rowcount == 0:
            return jsonify({"error": "Участник не найден"}), 404
        conn.execute("DELETE FROM games")
        return jsonify(state_payload(conn))


@app.post("/api/draw")
def api_generate_draw():
    with db() as conn:
        players = get_players(conn)
        n = len(players)
        if n not in draw_engine.supported_counts():
            return jsonify({
                "error": f"Для турнира нужно 12 участников (допускаются также 8 и 4), сейчас {n}"
            }), 400
        ratings = [p["rating"] for p in players]
        draw = draw_engine.build_draw(ratings)
        conn.execute("DELETE FROM games")
        ids = [p["id"] for p in players]
        for ri, games in enumerate(draw["rounds"], 1):
            for seat, g in enumerate(games):
                conn.execute(
                    "INSERT INTO games (rnd, seat, a1, a2, b1, b2, score_a) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (ri, seat, ids[g["a"][0]], ids[g["a"][1]], ids[g["b"][0]], ids[g["b"][1]]),
                )
        payload = state_payload(conn)
        payload["draw_info"] = {
            "design": draw["design"],
            "imbalance": round(draw["imbalance"], 2),
            "max_imbalance": round(draw["max_imbalance"], 2),
        }
        return jsonify(payload)


@app.delete("/api/draw")
def api_delete_draw():
    with db() as conn:
        conn.execute("DELETE FROM games")
        return jsonify(state_payload(conn))


@app.post("/api/games/<int:gid>/result")
def api_set_result(gid: int):
    data = request.get_json(silent=True) or {}
    try:
        score_a = int(data.get("score_a"))
    except (TypeError, ValueError):
        return jsonify({"error": "Очки должны быть целым числом"}), 400
    if not 0 <= score_a <= POINTS_PER_GAME:
        return jsonify({"error": f"Очки пары A должны быть от 0 до {POINTS_PER_GAME}"}), 400
    with db() as conn:
        cur = conn.execute("UPDATE games SET score_a = ? WHERE id = ?", (score_a, gid))
        if cur.rowcount == 0:
            return jsonify({"error": "Игра не найдена"}), 404
        return jsonify(state_payload(conn))


@app.delete("/api/games/<int:gid>/result")
def api_clear_result(gid: int):
    with db() as conn:
        cur = conn.execute("UPDATE games SET score_a = NULL WHERE id = ?", (gid,))
        if cur.rowcount == 0:
            return jsonify({"error": "Игра не найдена"}), 404
        return jsonify(state_payload(conn))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# Ініціалізація БД при імпорті модуля (потрібно для gunicorn/Render)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    ip = lan_ip()
    print("=" * 50)
    print("  Падель-турнир запущен!")
    print(f"  На телефоне откройте:  http://{ip}:{port}")
    print(f"  На компьютере:         http://127.0.0.1:{port}")
    print("  (телефон и компьютер должны быть в одной сети)")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, threaded=True)

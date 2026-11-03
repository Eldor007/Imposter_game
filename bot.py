"""Small Telegram Imposter game bot. Python 3.10+, no dependencies."""
import json
import logging
import os
import random
import secrets
import time
from pathlib import Path
from urllib import request, error

TOKEN = os.environ.get("BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
USERNAME = ""
STATE = Path(os.environ.get("STATE_FILE", "data/state.json"))
games = {}  # group_id -> {admin, players, stage, count, word, message_id}
pending = {}  # admin_id -> group_id
offset = None


def save():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps({"games": games, "pending": pending, "offset": offset}, default=list), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temp, STATE)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1)


def load():
    global games, pending, offset
    if not STATE.exists():
        return
    data = json.loads(STATE.read_text(encoding="utf-8"))
    games = {int(k): v for k, v in data["games"].items()}
    pending = {int(k): v for k, v in data["pending"].items()}
    offset = data["offset"]
    for game in games.values():
        game["players"] = {int(k): v for k, v in game["players"].items()}
        for key in ("imposters", "delivered"):
            if key in game:
                game[key] = set(game[key])


def api(method, **params):
    body = json.dumps(params).encode()
    req = request.Request(API + method, data=body, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=40) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        result = json.loads(exc.read())
        raise RuntimeError(f"Telegram {exc.code}: {result.get('description', 'API error')}") from None
    except (error.URLError, TimeoutError, OSError):
        raise RuntimeError("Telegram network error") from None
    if not result.get("ok"):
        raise RuntimeError(result.get("description", method))
    return result["result"]


def send(chat_id, text, **kwargs):
    return api("sendMessage", chat_id=chat_id, text=text, **kwargs)


def name(user):
    return user.get("first_name") or user.get("username") or str(user["id"])


def lobby_text(game):
    players = list(game["players"].values())
    names = ", ".join(name(p) for p in players) or "пока никого"
    return f"🎭 Импостер — набор игроков\nУчаствуют ({len(players)}): {names}\nНажмите кнопку ниже, затем откройте личный чат с ботом и нажмите Start."


def keyboard(game):
    return {"inline_keyboard": [[{"text": "🎮 Участвую / открыть бота", "url": f"https://t.me/{USERNAME}?start=join_{game['nonce']}"}], [{"text": "Выйти", "callback_data": "leave"}]]}


def ask_count(uid):
    send(uid, "Сколько импостеров? Выбери кнопку или отправь число от 1 до 20.", reply_markup={"inline_keyboard": [[{"text": str(n), "callback_data": f"count:{n}"} for n in (1, 2, 3)]]})


def refresh(group_id):
    game = games[group_id]
    try:
        api("editMessageText", chat_id=group_id, message_id=game["message_id"], text=lobby_text(game), reply_markup=keyboard(game))
    except RuntimeError as exc:
        if "message is not modified" not in str(exc):
            logging.warning("Lobby refresh: %s", exc)


def is_admin(group_id, user_id):
    return any(m["user"]["id"] == user_id for m in api("getChatAdministrators", chat_id=group_id))


def start_game(group_id):
    game = games[group_id]
    players = list(game["players"].values())
    count = game["count"]
    if len(players) < count + 1:
        send(game["admin"], f"Нужно минимум {count + 1} игроков; сейчас {len(players)}. Добавьте игроков и отправьте /begin снова.")
        return
    if "imposters" not in game:
        game["imposters"] = {p["id"] for p in random.SystemRandom().sample(players, count)}
        game["delivered"] = set()
        save()
    imposters = game["imposters"]
    delivered = []
    failed = []
    for player in players:
        if player["id"] in game["delivered"]:
            continue
        role = "🎭 Ты ИМПОСТЕР. Секретное слово тебе неизвестно." if player["id"] in imposters else f"🔒 Секретное слово: {game['word']}"
        try:
            send(player["id"], role, protect_content=True)
            game["delivered"].add(player["id"])
            save()
            delivered.append(player)
            time.sleep(0.05)
        except RuntimeError as exc:
            logging.warning("DM failed for user %s: %s", player["id"], exc)
            failed.append(player)
    if failed:
        send(group_id, "Не смог отправить роль: " + ", ".join(name(p) for p in failed) + ". Пусть они нажмут Start в личке с ботом; админ может запустить /begin повторно. Уже доставленные роли не меняются.")
        return
    game["stage"] = "done"
    save()
    send(group_id, f"✅ Роли отправлены {len(game['delivered'])} игрокам. Импостеров: {count}. Можно начинать! Следующая игра: /newgame")


def on_message(msg):
    chat = msg["chat"]
    user = msg.get("from")
    if not user or user.get("is_bot"):
        return
    uid, cid = user["id"], chat["id"]
    text = (msg.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
    if command and "@" in text.split(maxsplit=1)[0]:
        if text.split(maxsplit=1)[0].split("@", 1)[1].lower() != USERNAME.lower():
            return
    if chat["type"] == "private":
        if command == "/start":
            arg = text.partition(" ")[2]
            if arg.startswith(("join_", "setup_")):
                kind, nonce = arg.split("_", 1)
                match = next(((gid, g) for gid, g in games.items() if g["nonce"] == nonce), None)
                if not match:
                    send(cid, "Эта игра уже недоступна. Открой новую ссылку из группы.")
                    return
                gid, game = match
                if kind == "setup":
                    if uid != game["admin"] or not is_admin(gid, uid):
                        send(cid, "Настройка доступна создателю игры — админу группы.")
                    else:
                        pending[uid] = gid
                        if game["stage"] == "count":
                            ask_count(uid)
                        elif game["stage"] == "word":
                            send(uid, "Отправь секретное слово.")
                        else:
                            send(uid, "Настройка завершена. Для раздачи: /begin")
                    return
                if game["stage"] == "done" or "imposters" in game:
                    send(cid, "Раздача уже началась. Вступить можно в следующую игру.")
                    return
                if uid not in game["players"] and len(game["players"]) >= 50:
                    send(cid, "Лимит — 50 игроков.")
                    return
                game["players"][uid] = user
                send(cid, "✅ Ты участвуешь! Здесь придёт твоя роль после запуска админом.")
                refresh(gid)
                return
            send(cid, "🎭 Импостер. Нажми «Участвую» в группе, чтобы получить роль. Админ создаёт игру командой /newgame.", reply_markup={"inline_keyboard": [[{"text": "Добавить в группу", "url": f"https://t.me/{USERNAME}?startgroup=game"}]]})
            return
        if command == "/cancel":
            gid = pending.pop(uid, None)
            if gid in games:
                del games[gid]
            send(cid, "Настройка отменена.")
            return
        group_id = pending.get(uid)
        game = games.get(group_id) if group_id is not None else None
        if not game or game["admin"] != uid:
            send(cid, "Добавь меня в группу и запусти там /newgame. Перед этим нажми /start здесь.")
            return
        if game["stage"] == "count":
            if not text.isdecimal() or not 1 <= int(text) <= 20:
                send(cid, "Сколько импостеров? Отправь число от 1 до 20.")
                return
            game["count"] = int(text)
            game["stage"] = "word"
            send(cid, "Теперь отправь секретное слово или короткую фразу.")
        elif game["stage"] == "word":
            if not text or text.startswith("/") or len(text) > 100:
                send(cid, "Отправь слово или фразу до 100 символов.")
                return
            game["word"] = text
            game["stage"] = "ready"
            send(cid, f"Готово. Игроков сейчас: {len(game['players'])}. Когда все вступят, отправь /begin здесь или в группе.")
        elif command == "/begin" and game["stage"] == "ready":
            if is_admin(group_id, uid):
                start_game(group_id)
            else:
                send(uid, "Запускать игру может админ группы.")
        else:
            send(cid, "Игра готова. Отправь /begin, когда все игроки вступят.")
        return
    if chat["type"] not in ("group", "supergroup"):
        return
    if command == "/newgame":
        if not is_admin(cid, uid):
            send(cid, "Новую игру может создать только админ группы.")
            return
        if cid in games and games[cid]["stage"] not in ("done",):
            send(cid, "Сначала заверши текущую игру командой /stop.")
            return
        if uid in pending and pending[uid] != cid and pending[uid] in games and games[pending[uid]]["stage"] != "done":
            send(cid, "Сначала заверши настройку игры в другой группе или отправь /cancel в личке.")
            return
        game = {"admin": uid, "players": {}, "stage": "count", "count": None, "word": None, "nonce": secrets.token_urlsafe(12)}
        games[cid] = game
        pending[uid] = cid
        game["message_id"] = send(cid, lobby_text(game), reply_markup=keyboard(game))["message_id"]
        send(cid, "Админ, задай число импостеров и слово в личке:", reply_markup={"inline_keyboard": [[{"text": "⚙️ Настроить игру", "url": f"https://t.me/{USERNAME}?start=setup_{game['nonce']}"}]]})
    elif command == "/begin" and cid in games:
        game = games[cid]
        if uid != game["admin"] and not is_admin(cid, uid):
            return
        if game["stage"] != "ready":
            send(cid, "Сначала админ должен задать число импостеров и слово в личке с ботом.")
        else:
            start_game(cid)
    elif command == "/stop" and cid in games:
        game = games[cid]
        if uid == game["admin"] or is_admin(cid, uid):
            pending.pop(game["admin"], None)
            del games[cid]
            send(cid, "Игра отменена. Для новой игры: /newgame")


def on_callback(query):
    api("answerCallbackQuery", callback_query_id=query["id"])
    if query.get("data", "").startswith("count:"):
        uid = query["from"]["id"]
        game = games.get(pending.get(uid))
        if game and game["stage"] == "count":
            on_message({"chat": {"id": uid, "type": "private"}, "from": query["from"], "text": query["data"].split(":")[1]})
        return
    msg = query.get("message") or {}
    group_id = (msg.get("chat") or {}).get("id")
    game = games.get(group_id)
    if not game or game["stage"] == "done" or "imposters" in game or msg.get("message_id") != game.get("message_id"):
        return
    user = query["from"]
    if query.get("data") == "join":
        game["players"][user["id"]] = user
    elif query.get("data") == "leave":
        game["players"].pop(user["id"], None)
    refresh(group_id)


def main():
    global offset, USERNAME
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN in /etc/imposter-bot.env or environment")
    load()
    USERNAME = api("getMe")["username"]
    webhook = api("getWebhookInfo")
    if webhook.get("url"):
        raise SystemExit("This token has an active webhook; use a new dedicated bot or remove its webhook first")
    api("setMyCommands", commands=[{"command": c, "description": d} for c, d in [("newgame", "Создать игру (админ)"), ("begin", "Раздать роли (админ)"), ("stop", "Завершить игру (админ)"), ("start", "Открыть бота"), ("cancel", "Отменить настройку в личке")]])
    logging.info("Bot started")
    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        on_message(update["message"])
                    elif "callback_query" in update:
                        on_callback(update["callback_query"])
                    save()
                except Exception:
                    logging.exception("Update failed")
        except Exception:
            logging.exception("Polling failed; retrying")
            time.sleep(5)


if __name__ == "__main__":
    main()

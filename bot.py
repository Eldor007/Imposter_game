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
    status = "✅ Слово задано. Можно раздавать роли." if game['stage'] == 'ready' else "⚙️ Ведущий настраивает игру в личке."
    return f"🎭 ИМПОСТЕР\nВедущий: {game.get('host_name', 'создатель игры')}\n{status}\n\nИгроки ({len(players)}): {names}\n\nНажми «Участвую», затем Start в личке. Ведущий знает слово и не участвует в раздаче."


def keyboard(game):
    n = game['nonce']
    rows = [[{"text": "🎮 Участвую", "url": f"https://t.me/{USERNAME}?start=join_{n}"}], [{"text": "⚙️ Настроить игру", "url": f"https://t.me/{USERNAME}?start=setup_{n}"}]]
    if game['stage'] == 'ready':
        rows.append([{"text": "🚀 Раздать роли", "callback_data": f"begin:{n}"}])
    rows.append([{"text": "Выйти из игры", "callback_data": f"leave:{n}"}, {"text": "Отменить игру", "callback_data": f"stop:{n}"}])
    return {"inline_keyboard": rows}


def menu(cid):
    send(cid, "🎭 Импостер — игра в ассоциации.\n\nЛюбой участник может создать игру и стать ведущим. Он задаёт слово в личке; остальные получают слово или роль импостера.\n\nНажми кнопку, чтобы начать.", reply_markup={"inline_keyboard": [[{"text": "🎲 Новая игра", "callback_data": "newgame"}]]})


def controls(game):
    return {"inline_keyboard": [[{"text": "🚀 Раздать роли", "callback_data": f"begin:{game['nonce']}"}], [{"text": "Отменить игру", "callback_data": f"stop:{game['nonce']}"}]]}


def ask_count(uid):
    game = games[pending[uid]]
    send(uid, "Шаг 1 из 2. Сколько будет импостеров?\nВыбери кнопку или отправь число от 1 до 20.", reply_markup={"inline_keyboard": [[{"text": str(n), "callback_data": f"count:{game['nonce']}:{n}"} for n in (1, 2, 3)]]})


def refresh(group_id):
    game = games[group_id]
    try:
        api("editMessageText", chat_id=group_id, message_id=game["message_id"], text=lobby_text(game), reply_markup=keyboard(game))
    except RuntimeError as exc:
        if "message is not modified" not in str(exc):
            logging.warning("Lobby refresh: %s", exc)


def stop_game(gid):
    game = games.pop(gid)
    if pending.get(game['admin']) == gid:
        pending.pop(game['admin'])
    save()
    send(gid, "Игра завершена.")
    menu(gid)


def start_game(group_id):
    game = games[group_id]
    players = list(game["players"].values())
    count = game["count"]
    if len(players) < count + 1:
        send(group_id, f"Пока мало игроков: {len(players)}. Для {count} импостеров нужно минимум {count + 1} игроков. Нажимайте «Участвую» в сообщении выше.", reply_markup=controls(game))
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
        send(group_id, "Не смог отправить роль: " + ", ".join(name(p) for p in failed) + ". Откройте личку с ботом и нажмите Start. Затем ведущий нажимает «Раздать роли» ещё раз; назначенные роли сохранятся.", reply_markup=controls(game))
        return
    game["stage"] = "done"
    save()
    send(group_id, f"✅ Роли отправлены {len(game['delivered'])} игрокам. Импостеров: {count}.\n\nПо очереди называйте ассоциации. Импостеры пытаются не выдать себя. Затем обсудите, кто подозрителен!", reply_markup={"inline_keyboard": [[{"text": "🎲 Следующая игра", "callback_data": "newgame"}]]})


def on_message(msg):
    chat = msg['chat']
    user = msg.get('from')
    if not user or user.get('is_bot'):
        return
    uid, cid = user['id'], chat['id']
    text = (msg.get('text') or '').strip()
    command = text.split(maxsplit=1)[0].split('@')[0].lower() if text.startswith('/') else ''
    if command and '@' in text.split(maxsplit=1)[0]:
        if text.split(maxsplit=1)[0].split('@', 1)[1].lower() != USERNAME.lower():
            return
    if chat['type'] == 'private':
        if command in ('/start', '/help'):
            arg = text.partition(' ')[2]
            if arg.startswith(('join_', 'setup_')):
                kind, nonce = arg.split('_', 1)
                match = next(((gid, g) for gid, g in games.items() if g['nonce'] == nonce), None)
                if not match:
                    send(cid, 'Эта игра уже завершена. Нажми кнопку в новой игре в группе.')
                    return
                gid, game = match
                if kind == 'setup':
                    if uid != game['admin']:
                        send(cid, 'Настраивает игру её создатель. Для участия нажми «Участвую» в группе.')
                        return
                    if game['stage'] == 'done':
                        send(cid, 'Роли уже отправлены. Создать следующую игру можно в группе.')
                        return
                    pending[uid] = gid
                    if game['stage'] == 'count':
                        ask_count(uid)
                    elif game['stage'] == 'word':
                        send(cid, 'Шаг 2 из 2. Отправь секретное слово или короткую фразу. Слово увидят только обычные игроки.')
                    else:
                        send(cid, 'Всё готово! Когда игроки соберутся, нажми кнопку ниже.', reply_markup=controls(game))
                    return
                if game['stage'] == 'done' or 'imposters' in game:
                    send(cid, 'Раздача уже началась. Присоединяйся к следующей игре.')
                    return
                if uid == game['admin']:
                    send(cid, 'Ты ведущий и знаешь слово. Собери остальных игроков, затем раздай роли.', reply_markup=controls(game))
                    return
                if uid not in game['players'] and len(game['players']) >= 50:
                    send(cid, 'Уже набралось 50 игроков. Присоединяйся в следующем раунде.')
                    return
                game['players'][uid] = user
                save()
                send(cid, '✅ Ты в игре! Вернись в группу. Я пришлю сюда роль, когда ведущий начнёт раздачу.')
                refresh(gid)
                return
            gid = pending.get(uid)
            if gid in games and games[gid]['stage'] == 'count':
                ask_count(uid)
                return
            send(cid, '🎭 Привет! Добавь меня в группу, затем нажми «Новая игра». Стать ведущим может любой участник. Чтобы играть, нажми «Участвую» в группе.', reply_markup={'inline_keyboard': [[{'text': '➕ Добавить в группу', 'url': f'https://t.me/{USERNAME}?startgroup=game'}]]})
            return
        gid = pending.get(uid)
        game = games.get(gid)
        if command in ('/cancel', '/stop'):
            if game and game['admin'] == uid:
                stop_game(gid)
            send(cid, 'Готово. Новую игру можно создать в группе.')
            return
        if not game or game['admin'] != uid:
            send(cid, 'Чтобы играть, нажми «Участвую» в группе. Чтобы стать ведущим, создай там новую игру: /newgame')
            return
        if command == '/begin':
            begin(gid, uid)
            return
        if command:
            send(cid, 'Следуй шагам настройки или отправь /cancel для отмены.')
            return
        if game['stage'] == 'count':
            if not text.isdecimal() or len(text) > 2 or not 1 <= int(text) <= 20:
                ask_count(uid)
                return
            game['count'] = int(text)
            game['stage'] = 'word'
            send(cid, 'Шаг 2 из 2. Отправь секретное слово или короткую фразу (до 100 символов).')
        elif game['stage'] == 'word':
            if not text or len(text) > 100:
                send(cid, 'Нужно слово или фраза до 100 символов.')
                return
            game['word'] = text
            game['stage'] = 'ready'
            save()
            refresh(gid)
            send(cid, f"✅ Готово! Импостеров: {game['count']}. Игроков сейчас: {len(game['players'])}.\nКогда все подключатся, нажми «Раздать роли».", reply_markup=controls(game))
        elif game['stage'] == 'ready':
            send(cid, 'Игра настроена. Ждём игроков!', reply_markup=controls(game))
        else:
            send(cid, 'Роли уже отправлены. Следующую игру можно создать в группе.')
        return
    if chat['type'] not in ('group', 'supergroup'):
        return
    if command in ('/start', '/help') or any(u.get('username', '').lower() == USERNAME.lower() for u in msg.get('new_chat_members', [])):
        menu(cid)
    elif command == '/newgame':
        if cid in games and games[cid]['stage'] != 'done':
            send(cid, 'Здесь уже собирается игра. Нажми «Участвую» выше. Настраивать и запускать её может создатель.')
            return
        if uid in pending and pending[uid] != cid and pending[uid] in games and games[pending[uid]]['stage'] != 'done':
            send(cid, 'Ты уже ведущий в другой группе. Сначала заверши ту игру или отправь /cancel в личке.')
            return
        game = {'admin': uid, 'host_name': name(user), 'players': {}, 'stage': 'count', 'count': None, 'word': None, 'nonce': secrets.token_urlsafe(12)}
        games[cid] = game
        pending[uid] = cid
        game['message_id'] = send(cid, lobby_text(game), reply_markup=keyboard(game))['message_id']
        save()
    elif command == '/begin':
        begin(cid, uid)
    elif command == '/stop':
        game = games.get(cid)
        if game and uid == game['admin']:
            stop_game(cid)
        elif game:
            send(cid, 'Завершить игру может её создатель.')
        else:
            menu(cid)


def begin(gid, uid):
    game = games.get(gid)
    if not game:
        return
    if uid != game['admin']:
        send(gid, 'Раздать роли может только создатель этой игры.')
    elif game['stage'] == 'ready':
        start_game(gid)
    elif game['stage'] == 'done':
        send(gid, 'Роли уже отправлены. Можно играть!')
    else:
        send(gid, 'Сначала ведущий задаёт число импостеров и слово. Нажми «Настроить игру» в сообщении выше.')


def on_callback(query):
    data = query.get('data', '')
    user = query['from']
    uid = user['id']
    msg = query.get('message') or {}
    chat = msg.get('chat') or {}
    if data == 'newgame':
        api('answerCallbackQuery', callback_query_id=query['id'])
        if chat.get('type') in ('group', 'supergroup'):
            on_message({'chat': chat, 'from': user, 'text': '/newgame'})
        return
    parts = data.split(':')
    match = next(((gid, g) for gid, g in games.items() if len(parts) > 1 and g['nonce'] == parts[1]), None)
    if not match:
        api('answerCallbackQuery', callback_query_id=query['id'], text='Кнопка от старой игры. Открой текущую игру в группе.', show_alert=True)
        return
    gid, game = match
    action = parts[0]
    if action in ('count', 'begin', 'stop') and uid != game['admin']:
        api('answerCallbackQuery', callback_query_id=query['id'], text='Этой игрой управляет её создатель.', show_alert=True)
        return
    api('answerCallbackQuery', callback_query_id=query['id'])
    if action == 'count' and game['stage'] == 'count' and len(parts) == 3:
        pending[uid] = gid
        on_message({'chat': {'id': uid, 'type': 'private'}, 'from': user, 'text': parts[2]})
    elif action == 'begin':
        begin(gid, uid)
    elif action == 'stop':
        stop_game(gid)
    elif action == 'leave' and game['stage'] != 'done' and 'imposters' not in game:
        game['players'].pop(uid, None)
        refresh(gid)
    save()


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
    api("setMyCommands", commands=[{"command": c, "description": d} for c, d in [("newgame", "Создать игру — стать ведущим"), ("begin", "Раздать роли (ведущий)"), ("stop", "Завершить игру (ведущий)"), ("start", "Открыть бота"), ("cancel", "Отменить настройку в личке")]])
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

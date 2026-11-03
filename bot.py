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
        # Previous releases used 'done' for roles dealt, before results existed.
        if game['stage'] == 'done':
            game['stage'] = 'playing'
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
    stage = game['stage']
    status = {'count': '⚙️ Ведущий выбирает число импостеров', 'word': '✍️ Ведущий задаёт слово', 'ready': '🟢 Лобби открыто — ждём игроков', 'playing': '🎮 Игра идёт — обсуждайте ассоциации', 'finished': '🏁 Игра завершена', 'cancelled': 'Игра отменена'}.get(stage, 'Игра')
    if 'imposters' in game and stage == 'ready':
        status = '📨 Раздача ролей — вход закрыт'
    roster = '\n'.join(f"{i}. {name(p)[:45]}" for i, p in enumerate(players, 1)) or 'Пока никого — нажми «Участвую»!'
    count = game.get('count')
    details = f"\nИмпостеров: {count}" if count else ''
    if 'imposters' in game:
        details += f"\nРоли доставлены: {len(game.get('delivered', []))}/{len(players)}"
    elif count:
        details += f"\nДо минимального состава: {max(0, count + 1 - len(players))}"
    result = ''
    if stage == 'finished':
        imposters = ', '.join(name(p)[:45] for p in players if p['id'] in game.get('imposters', set()))
        result = f"\n\n🔓 Слово: {game['word']}\n🎭 Импостеры: {imposters}"
    return f"🎭 ИМПОСТЕР — ТАБЛО\nВедущий: {game.get('host_name', 'создатель игры')}\n{status}{details}\n\nИгроки ({len(players)}):\n{roster}{result}"


def keyboard(game):
    n = game['nonce']
    status_row = [{"text": "🔄 Обновить статус", "callback_data": f"status:{n}"}]
    if game['stage'] in ('finished', 'cancelled'):
        return {"inline_keyboard": [[{"text": "🎲 Следующая игра", "callback_data": "newgame"}], status_row]}
    if game['stage'] == 'playing':
        return {"inline_keyboard": [status_row, [{"text": "🏁 Завершить и показать результат", "callback_data": f"finish:{n}"}]]}
    if 'imposters' in game:
        return controls(game)
    rows = [[{"text": "🎮 Участвую", "url": f"https://t.me/{USERNAME}?start=join_{n}"}], [{"text": "⚙️ Настроить игру", "url": f"https://t.me/{USERNAME}?start=setup_{n}"}]]
    if game['stage'] == 'ready':
        rows.append([{"text": "🚀 Раздать роли", "callback_data": f"begin:{n}"}])
    rows.append([{"text": "Выйти из игры", "callback_data": f"leave:{n}"}, {"text": "Отменить игру", "callback_data": f"stop:{n}"}])
    rows.append(status_row)
    return {"inline_keyboard": rows}


def menu(cid):
    send(cid, "🎭 Импостер — игра в ассоциации.\n\nЛюбой участник может создать игру и стать ведущим. Он задаёт слово в личке; остальные получают слово или роль импостера.\n\nНажми кнопку, чтобы начать.", reply_markup={"inline_keyboard": [[{"text": "🎲 Новая игра", "callback_data": "newgame"}]]})


def controls(game):
    return {"inline_keyboard": [[{"text": "🚀 Раздать роли", "callback_data": f"begin:{game['nonce']}"}], [{"text": "Отменить игру", "callback_data": f"stop:{game['nonce']}"}]]}


def host_keyboard(game):
    n = game['nonce']
    rows = [[{'text': '🔄 Обновить статус', 'callback_data': f'status:{n}'}]]
    if game['stage'] == 'ready':
        rows.insert(0, [{'text': '🚀 Раздать роли', 'callback_data': f'begin:{n}'}])
    elif game['stage'] == 'playing':
        rows.append([{'text': '🏁 Завершить и показать результат', 'callback_data': f'finish:{n}'}])
    elif game['stage'] in ('count', 'word'):
        rows.insert(0, [{'text': '⚙️ Продолжить настройку', 'url': f'https://t.me/{USERNAME}?start=setup_{n}'}])
    if game['stage'] not in ('playing', 'finished', 'cancelled'):
        rows.append([{'text': 'Отменить игру', 'callback_data': f'stop:{n}'}])
    return {'inline_keyboard': rows}


def refresh_host(gid, message_id=None):
    game = games[gid]
    ids = [message_id] if message_id else game.get('host_panel_ids', [])
    for mid in ids:
        try:
            api('editMessageText', chat_id=game['admin'], message_id=mid,
                text=lobby_text(game), reply_markup=host_keyboard(game))
        except RuntimeError as exc:
            if 'message is not modified' not in str(exc):
                logging.warning('Host panel refresh: %s', exc)


def publish_host_status(gid):
    game = games[gid]
    message = send(game['admin'], lobby_text(game), reply_markup=host_keyboard(game))
    if isinstance(message, dict) and message.get('message_id'):
        game['host_panel_ids'] = (game.get('host_panel_ids', []) + [message['message_id']])[-3:]
        save()


def ask_count(uid):
    game = games[pending[uid]]
    send(uid, "Шаг 1 из 2. Сколько будет импостеров?\nВыбери кнопку или отправь число от 1 до 20.", reply_markup={"inline_keyboard": [[{"text": str(n), "callback_data": f"count:{game['nonce']}:{n}"} for n in (1, 2, 3)]]})


def refresh(group_id, message_id=None):
    game = games[group_id]
    ids = [message_id] if message_id else list(dict.fromkeys([game.get('message_id')] + game.get('status_ids', [])))
    for mid in ids:
        if not mid:
            continue
        try:
            api("editMessageText", chat_id=group_id, message_id=mid, text=lobby_text(game), reply_markup=keyboard(game))
        except RuntimeError as exc:
            if "message is not modified" not in str(exc):
                logging.warning("Lobby refresh: %s", exc)
    if message_id is None:
        refresh_host(group_id)


def publish_status(gid):
    game = games[gid]
    message = send(gid, lobby_text(game), reply_markup=keyboard(game))
    if isinstance(message, dict) and message.get('message_id'):
        game['status_ids'] = (game.get('status_ids', []) + [message['message_id']])[-3:]
        save()


def stop_game(gid):
    if 'imposters' in games[gid]:
        finish_game(gid)
        return
    games[gid]['stage'] = 'cancelled'
    refresh(gid)
    game = games.pop(gid)
    if pending.get(game['admin']) == gid:
        pending.pop(game['admin'])
    save()
    send(gid, "Игра завершена.")
    menu(gid)


def finish_game(gid):
    game = games[gid]
    if game['stage'] == 'finished':
        return
    if 'imposters' not in game:
        send(gid, 'Роли ещё не раздавались. Пока можно только отменить лобби.')
        return
    game['stage'] = 'finished'
    save()
    refresh(gid)
    publish_status(gid)


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
        refresh(group_id)
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
        refresh(group_id)
        send(group_id, "Не смог отправить роль: " + ", ".join(name(p) for p in failed) + ". Откройте личку с ботом и нажмите Start. Затем ведущий нажимает «Раздать роли» ещё раз; назначенные роли сохранятся.", reply_markup=controls(game))
        return
    game["stage"] = "playing"
    save()
    refresh(group_id)
    publish_status(group_id)


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
                    if game['stage'] in ('playing', 'finished'):
                        publish_host_status(gid)
                        return
                    pending[uid] = gid
                    if game['stage'] == 'count':
                        ask_count(uid)
                    elif game['stage'] == 'word':
                        send(cid, 'Шаг 2 из 2. Отправь секретное слово или короткую фразу. Слово увидят только обычные игроки.')
                    else:
                        publish_host_status(gid)
                    return
                if game['stage'] in ('playing', 'finished') or 'imposters' in game:
                    send(cid, 'Раздача уже началась. Присоединяйся к следующей игре.')
                    return
                if uid == game['admin']:
                    publish_host_status(gid)
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
            if gid in games and games[gid]['admin'] == uid:
                publish_host_status(gid)
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
        if command in ('/status', '/players', '/results'):
            publish_host_status(gid)
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
            refresh(gid)
            send(cid, 'Шаг 2 из 2. Отправь секретное слово или короткую фразу (до 100 символов).')
        elif game['stage'] == 'word':
            if not text or len(text) > 100:
                send(cid, 'Нужно слово или фраза до 100 символов.')
                return
            game['word'] = text
            game['stage'] = 'ready'
            save()
            refresh(gid)
            publish_host_status(gid)
        elif game['stage'] == 'ready':
            publish_host_status(gid)
        else:
            send(cid, 'Роли уже отправлены. Следующую игру можно создать в группе.')
        return
    if chat['type'] not in ('group', 'supergroup'):
        return
    if command in ('/status', '/players', '/results'):
        if cid in games:
            refresh(cid)
            publish_status(cid)
        else:
            menu(cid)
    elif command == '/finish':
        if cid in games and uid == games[cid]['admin']:
            finish_game(cid)
        else:
            send(cid, 'Показать результат может только создатель текущей игры.')
    elif command in ('/start', '/help') or any(u.get('username', '').lower() == USERNAME.lower() for u in msg.get('new_chat_members', [])):
        menu(cid)
    elif command == '/newgame':
        if cid in games and games[cid]['stage'] != 'finished':
            send(cid, 'Игра уже создана. Посмотреть участников: /status. Перед новой игрой ведущий должен завершить текущую.')
            return
        if uid in pending and pending[uid] != cid and pending[uid] in games and games[pending[uid]]['stage'] != 'finished':
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
    elif game['stage'] == 'playing':
        send(gid, 'Роли уже отправлены. Можно играть!')
    elif game['stage'] == 'finished':
        send(gid, 'Игра завершена. Нажми «Следующая игра» в табло.')
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
    if action in ('count', 'begin', 'stop', 'finish') and uid != game['admin']:
        api('answerCallbackQuery', callback_query_id=query['id'], text='Этой игрой управляет её создатель.', show_alert=True)
        return
    api('answerCallbackQuery', callback_query_id=query['id'])
    if uid == game['admin'] and chat.get('type') == 'private' and chat.get('id') == uid and msg.get('message_id') and action in ('status', 'begin', 'finish', 'stop'):
        game['host_panel_ids'] = list(dict.fromkeys(game.get('host_panel_ids', []) + [msg['message_id']]))[-3:]
        refresh_host(gid, msg['message_id'])
    if action == 'count' and game['stage'] == 'count' and len(parts) == 3:
        pending[uid] = gid
        on_message({'chat': {'id': uid, 'type': 'private'}, 'from': user, 'text': parts[2]})
    elif action == 'begin':
        begin(gid, uid)
    elif action == 'stop':
        stop_game(gid)
    elif action == 'finish':
        finish_game(gid)
    elif action == 'status':
        if chat.get('id') == gid:
            refresh(gid, msg.get('message_id'))
    elif action == 'leave' and game['stage'] not in ('playing', 'finished') and 'imposters' not in game:
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
    api("setMyCommands", commands=[{"command": c, "description": d} for c, d in [("newgame", "Создать игру — стать ведущим"), ("status", "Табло: игроки и состояние игры"), ("results", "Результат завершённой игры"), ("finish", "Завершить и раскрыть роли (ведущий)"), ("begin", "Раздать роли (ведущий)"), ("stop", "Завершить игру (ведущий)"), ("start", "Открыть бота"), ("cancel", "Отменить настройку в личке")]])
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

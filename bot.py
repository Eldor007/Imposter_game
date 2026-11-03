"""Telegram Imposter. Python 3.10+, standard library only."""
import argparse
import json
import logging
import os
import time
from urllib import error, request

import game
import ui
from storage import Store


class ApiError(RuntimeError):
    def __init__(self, message, code=0, retry_after=0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token):
        self.base = f'https://api.telegram.org/bot{token}/'
        self.last_call = 0

    def call(self, method, **params):
        if method != 'getUpdates':
            time.sleep(max(0, 0.045 - (time.monotonic() - self.last_call)))
        req = request.Request(self.base + method, data=json.dumps(params).encode(),
                              headers={'Content-Type': 'application/json'})
        try:
            with request.urlopen(req, timeout=20) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except (ValueError, OSError):
                raise ApiError('Telegram HTTP error', exc.code) from None
            raise ApiError(result.get('description', 'Telegram error'), exc.code,
                           result.get('parameters', {}).get('retry_after', 0)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ApiError('Telegram недоступен по сети. Повторим позже.') from None
        finally:
            self.last_call = time.monotonic()
        if not result.get('ok'):
            raise ApiError(result.get('description', 'Telegram error'))
        return result['result']


class Bot:
    def __init__(self, store, api, username, clock=time.time):
        self.store, self.api, self.username, self.clock = store, api, username, clock
        self.dirty = set(store.games)
        self.cache = {}
        self.last_edit = {}
        self.cooldown = {}
        self.global_cooldown = 0
        self.last_timer = -1

    def save(self):
        self.store.save()

    def touch(self, gid):
        self.dirty.add(gid)
        self.save()

    def upsert(self, cid, mid, text, keyboard, force=False, private=False):
        """Edit one tracked message; only send if it doesn't exist any more."""
        signature = (text, json.dumps(keyboard, ensure_ascii=False, sort_keys=True))
        key = (cid, mid)
        at = self.clock()
        if mid and self.cache.get(key) == signature:
            return mid, True
        if at < self.global_cooldown or at < self.cooldown.get(key, 0):
            if force:
                raise ApiError('Telegram просит немного подождать. Нажми ещё раз позже.')
            return mid, False
        if mid and not force and at - self.last_edit.get(key, 0) < (1.1 if private else 3.2):
            return mid, False
        params = {'chat_id': cid, 'text': text, 'reply_markup': keyboard,
                  'link_preview_options': {'is_disabled': True}}
        try:
            if mid:
                try:
                    self.api.call('editMessageText', message_id=mid, **params)
                except ApiError as exc:
                    if 'message is not modified' in str(exc).lower():
                        pass
                    elif exc.code == 400 and any(t in str(exc).lower() for t in
                            ("message to edit not found", "message can't be edited", 'message_id_invalid')):
                        mid = None
                    else:
                        raise
            if not mid:
                message = self.api.call('sendMessage', protect_content=private,
                                        disable_notification=True, **params)
                mid = message['message_id']
        except ApiError as exc:
            self.cooldown[key] = at + max(exc.retry_after, 10 if exc.code != 403 else 120)
            if exc.retry_after:
                self.global_cooldown = at + exc.retry_after
            raise
        self.cache[(cid, mid)] = signature
        self.last_edit[(cid, mid)] = at
        return mid, True

    def group_panel(self, gid, force=False):
        g = self.store.games.get(gid)
        if g:
            text, keyboard = ui.group_panel(g, self.username, self.clock())
            mid = g.get('message_id') or self.store.data['boards'].get(gid)
        else:
            text = '🎭 ИМПОСТЕР\n\nЗнаешь слово? Дай ассоциацию и вычисли тех, кто притворяется.\n\nСоздать игру может любой участник. Всё управление — кнопками. Личные роли и голоса бот пришлёт в личку.'
            keyboard = ui.kb([[ui.button('🎲 Новая игра', 'newgame')],
                              [ui.url('📖 Как играть', f'https://t.me/{self.username}?start=rules')]])
            mid = self.store.data['boards'].get(gid)
        new_mid, complete = self.upsert(gid, mid, text, keyboard, force=force)
        if new_mid:
            self.store.data['boards'][gid] = new_mid
            if g:
                g['message_id'] = new_mid
        if new_mid != mid:
            self.save()
        return complete

    def private_panel(self, gid, uid, force=False):
        g = self.store.games[gid]
        g.setdefault('panels', {})
        text, keyboard = ui.private_panel(g, uid, self.username, self.clock())
        mid = g['panels'].get(uid)
        new_mid, complete = self.upsert(uid, mid, text, keyboard, force=force, private=True)
        if new_mid:
            g['panels'][uid] = new_mid
        if new_mid != mid:
            self.save()
        return complete

    def home(self, uid, rules=False):
        if rules:
            text = ui.RULES
            rows = [[ui.button('← Мои игры', 'home')]]
        else:
            text = '🎭 ИМПОСТЕР\n\nНайди игру в группе или добавь меня в свою. Чтобы присоединиться, нажми «Участвую» в групповом табло.'
            rows = []
            stats = self.store.data['stats'].get(uid)
            if stats:
                text += f"\n\nТвои результаты: игр {stats['games']}, побед {stats['wins']}."
            for g in list(self.store.games.values())[-15:]:
                if uid == g['admin'] or uid in g['players']:
                    rows.append([ui.button((g.get('group_title') or 'Моя игра')[:35], ui.code(g, 'open'))])
            rows += [[ui.url('➕ Добавить в группу', f'https://t.me/{self.username}?startgroup=game')],
                     [ui.button('📖 Правила', 'rules_home')]]
        mid = self.store.data['homes'].get(uid)
        new_mid, _ = self.upsert(uid, mid, text, ui.kb(rows), force=True, private=True)
        self.store.data['homes'][uid] = new_mid
        self.save()

    def find(self, nonce):
        return next(((gid, g) for gid, g in self.store.games.items() if g['nonce'] == nonce), None)

    def focus(self, uid, gid):
        self.store.data['pending'][uid] = gid

    def focused(self, uid):
        gid = self.store.data['pending'].get(uid)
        g = self.store.games.get(gid)
        if g and (uid == g['admin'] or uid in g['players']):
            return gid, g
        matches = [(gid, g) for gid, g in self.store.games.items()
                   if uid == g['admin'] or uid in g['players']]
        return matches[-1] if matches else (None, None)

    def create(self, gid, user, title='', rematch=False):
        old = self.store.games.get(gid)
        if old and old['stage'] not in game.END_STAGES:
            raise game.GameError('Игра уже идёт. Участвуй или дождись результата.')
        if old:
            self.score(old)
        for other_gid, other in self.store.games.items():
            if other_gid != gid and other['admin'] == user['id'] and other['stage'] not in game.END_STAGES:
                raise game.GameError('Ты уже управляешь игрой в другой группе. Сначала заверши её.')
        g = game.fresh(user, title or (old or {}).get('group_title', ''), old, rematch)
        self.store.games[gid] = g
        self.focus(user['id'], gid)
        self.touch(gid)
        return g

    def deliver(self, gid, retry=True):
        g = self.store.games[gid]
        self.save()  # Persist assignments before the first network delivery.
        failed = set(g.get('delivery_errors', []))
        attempted = 0
        for uid in g['players']:
            if uid in g['delivered']:
                continue
            if not retry and uid in failed:
                continue
            if attempted >= 8:
                break
            attempted += 1
            try:
                self.private_panel(gid, uid, force=True)
                g['delivered'].append(uid)
                failed.discard(uid)
                self.save()
            except ApiError as exc:
                logging.warning('Role delivery failed for user %s: %s', uid, exc)
                failed.add(uid)
                if exc.code == 0 or exc.retry_after:
                    # Avoid serial network timeouts for every remaining player.
                    failed.update(set(g['players']) - set(g['delivered']))
                    break
        g['delivery_errors'] = sorted(failed)
        g['delivery_auto'] = bool(set(g['players']) - set(g['delivered']) - failed)
        if set(g['delivered']) == set(g['players']):
            g['delivery_auto'] = False
            game.dealt(g, self.clock())
        self.touch(gid)

    def score(self, g):
        if g['stage'] != 'finished' or g.get('winner') not in ('players', 'imposters') or g.get('scored'):
            return
        for uid in g['players']:
            stat = self.store.data['stats'].setdefault(uid, {'games': 0, 'wins': 0})
            stat['games'] += 1
            if (uid in g['imposters']) == (g['winner'] == 'imposters'):
                stat['wins'] += 1
        g['scored'] = True
        self.save()

    def flush(self):
        """Coalesce bursts and edit at most 16 panels per pass."""
        budget = 16
        for gid in list(self.dirty):
            g = self.store.games.get(gid)
            if not g:
                self.dirty.discard(gid)
                continue
            self.score(g)
            complete = True
            try:
                complete = self.group_panel(gid)
            except ApiError as exc:
                logging.warning('Group panel: %s', exc)
                complete = False
            for uid in list(g.get('panels', {})):
                if g['stage'] == 'dealing' and uid in g['players'] and uid not in g.get('delivered', []):
                    continue  # Explicit delivery retry records whether the role arrived.
                text, keyboard = ui.private_panel(g, uid, self.username, self.clock())
                key = (uid, g['panels'][uid])
                signature = (text, json.dumps(keyboard, ensure_ascii=False, sort_keys=True))
                if self.cache.get(key) == signature:
                    continue
                if budget <= 0:
                    complete = False
                    break
                budget -= 1
                try:
                    complete = self.private_panel(gid, uid) and complete
                except ApiError as exc:
                    logging.warning('Private panel: %s', exc)
                    complete = False
            if complete:
                self.dirty.discard(gid)
            if budget <= 0:
                break

    def timers(self):
        at = self.clock()
        bucket = int(at // 15)
        changed = False
        for gid, g in self.store.games.items():
            if g['stage'] == 'dealing' and g.get('delivery_auto'):
                self.deliver(gid, retry=False)
            if game.tick(g, at):
                g['views'] = {} if g['stage'] != 'voting' else g.get('views', {})
                changed = True
                self.dirty.add(gid)
            elif bucket != self.last_timer and g.get('deadline'):
                self.dirty.add(gid)
        self.last_timer = bucket
        if changed:
            self.save()

    def stop(self, gid, uid):
        g = self.store.games[gid]
        game.owner(g, uid)
        if g['stage'] in game.END_STAGES:
            return
        game.finish(g, 'stopped', 'Создатель завершил игру досрочно.')
        self.touch(gid)

    def act(self, gid, uid, action, arg=None, user=None):
        g = self.store.games[gid]
        views = g.setdefault('views', {})
        if action == 'mode':
            game.choose_mode(g, uid, arg)
            if arg == 'random':
                game.join(g, user)
        elif action == 'count':
            game.set_count(g, uid, int(arg))
        elif action == 'join':
            game.join(g, user)
        elif action == 'leave':
            game.leave(g, uid)
        elif action == 'begin':
            game.prepare(g, uid)
            self.deliver(gid)
        elif action == 'said':
            game.clue(g, uid, at=self.clock())
        elif action == 'advance':
            game.advance(g, uid, self.clock())
        elif action == 'ready':
            game.ready_vote(g, uid, self.clock())
        elif action in ('ballot', 'vp'):
            if g['stage'] != 'voting' or uid not in game.live(g):
                raise game.GameError('Голосуют только оставшиеся игроки.')
            views[uid] = f'ballot:{max(0, min(6, int(arg or 0)))}'
        elif action == 'vote':
            epoch = g['epoch']
            game.cast_vote(g, uid, int(arg), self.clock())
            if epoch == g['epoch']:
                views[uid] = ''
        elif action == 'roster':
            views[uid] = f'roster:{max(0, min(4, int(arg or 0)))}'
        elif action == 'rules':
            views[uid] = 'rules'
        elif action in ('panel', 'open', 'status'):
            views[uid] = ''
        elif action in ('abort', 'finish', 'stop'):
            game.owner(g, uid)
            views[uid] = 'abort'
        elif action == 'confirm':
            game.owner(g, uid)
            if views.get(uid) != 'abort':
                raise game.GameError('Сначала нажми «Завершить досрочно».')
            self.stop(gid, uid)
        else:
            raise game.GameError('Эта кнопка устарела. Открой актуальную панель.')
        self.focus(uid, gid)
        self.touch(gid)

    def callback(self, q):
        uid = q['from']['id']
        data = q.get('data', '')
        message = q.get('message') or {}
        chat = message.get('chat') or {}
        private = chat.get('type') == 'private'
        answered = False

        def answer(text='', alert=False):
            nonlocal answered
            answered = True
            self.api.call('answerCallbackQuery', callback_query_id=q['id'], text=text[:190], show_alert=alert)

        try:
            if data in ('home', 'rules_home'):
                answer()
                if private:
                    self.home(uid, data == 'rules_home')
                return
            if data == 'newgame':
                if private or chat.get('type') not in ('group', 'supergroup'):
                    raise game.GameError('Создай игру кнопкой в своей группе.')
                self.create(chat['id'], q['from'], chat.get('title', ''))
                answer('Игра создана. Нажми «Создателю: настройка».')
                return
            parts = data.split(':')
            if len(parts) < 2:
                raise game.GameError('Старая кнопка. Открой текущую игру.')
            action, nonce = parts[:2]
            match = self.find(nonce)
            if not match:
                raise game.GameError('Это кнопка от прошлой игры. Открой актуальное табло в группе.')
            gid, g = match
            if not private and chat.get('id') != gid:
                raise game.GameError('Открой игру в её группе.')
            if private and chat.get('id') != uid:
                raise game.GameError('Открой бота в своей личке.')
            arg = parts[2] if len(parts) > 2 else None
            phase_actions = {'mode', 'count', 'said', 'advance', 'ready', 'ballot', 'vp', 'vote'}
            if action in phase_actions:
                # Old count buttons carried no phase. Only accept their old exact setup stage.
                if action == 'count' and len(parts) == 3 and g['stage'] == 'count':
                    arg = parts[2]
                else:
                    if len(parts) < 3 or int(parts[2]) != g['epoch']:
                        raise game.GameError('Этап уже сменился. Панель обновляется; нажми актуальную кнопку.')
                    arg = parts[3] if len(parts) > 3 else None
            if action == 'rematch':
                if g['stage'] not in game.END_STAGES:
                    raise game.GameError('Сначала закончите текущую игру.')
                game.owner(g, uid)
                g = self.create(gid, q['from'], rematch=True)
                answer('Реванш создан! Состав сохранён.')
                if private:
                    self.private_panel(gid, uid, force=True)
                return
            if action == 'vote' and not private:
                raise game.GameError('Выбирай игрока в личной панели.')
            if private:
                if uid != g['admin'] and uid not in g['players'] and action not in ('join', 'rules', 'panel', 'open', 'status'):
                    raise game.GameError('Сначала вступи в игру.')
                if action != 'open' and message.get('message_id') != self.store.data['homes'].get(uid) and message.get('message_id'):
                    g.setdefault('panels', {})[uid] = message['message_id']
            if action == 'begin':
                game.owner(g, uid)
                # Answer before potentially long delivery to all participants.
                game.prepare(g, uid)
                self.touch(gid)
                answer('Раздаю роли…')
                self.deliver(gid)
            else:
                self.act(gid, uid, action, arg, q['from'])
                answer('Голос принят' if action == 'vote' else '')
            if private:
                self.private_panel(gid, uid, force=True)
            elif action in ('abort', 'stop', 'finish'):
                try:
                    self.private_panel(gid, uid, force=True)
                except ApiError:
                    pass  # Group creator can always reach setup via the deep link.
        except (game.GameError, ValueError) as exc:
            if not answered:
                answer(str(exc) if isinstance(exc, game.GameError) else 'Эта кнопка устарела.', True)
        except ApiError as exc:
            logging.warning('Callback: %s', exc)
            if not answered:
                answer('Telegram временно недоступен. Попробуй ещё раз.', True)

    def private_message(self, m, command, text):
        uid = m['from']['id']
        # A new incoming private interaction proves the bot is unblocked again.
        self.cooldown = {key: value for key, value in self.cooldown.items() if key[0] != uid}
        arg = text.partition(' ')[2]
        if command == '/start' and arg == 'rules':
            self.home(uid, rules=True)
            return
        match = None
        kind = ''
        if command == '/start' and arg.startswith(('join_', 'setup_', 'play_', 'vote_')):
            kind, nonce = arg.split('_', 1)
            match = self.find(nonce)
            if not match:
                self.home(uid)
                return
        gid, g = match or self.focused(uid)
        if not g:
            self.home(uid)
            return
        self.focus(uid, gid)
        if kind == 'join':
            if uid in g['players'] and g['stage'] not in game.LOBBY_STAGES:
                pass  # Open an existing role, including a failed delivery.
            elif uid == g['admin'] and g['mode'] == 'manual':
                pass
            else:
                game.join(g, m['from'])
        if kind == 'setup' and uid != g['admin']:
            # A mistaken click still opens their own public/personal game panel.
            g.setdefault('views', {})[uid] = ''
        if kind == 'vote' and g['stage'] == 'voting' and uid in game.live(g):
            g.setdefault('views', {})[uid] = 'ballot:0'
        elif command in ('/start', '/status', '/players', '/results', '/help'):
            g.setdefault('views', {})[uid] = ''
            if kind == 'vote' and g['stage'] == 'voting' and uid in game.live(g):
                g['views'][uid] = 'ballot:0'
        elif command in ('/cancel', '/stop', '/finish'):
            self.act(gid, uid, 'abort')
        elif command == '/begin':
            self.act(gid, uid, 'begin')
        elif not command:
            if uid == g['admin'] and g['stage'] == 'count':
                if not text.isdecimal() or len(text) > 2:
                    raise game.GameError('Выбери кнопку или отправь число от 1 до 20.')
                game.set_count(g, uid, int(text))
            elif uid == g['admin'] and g['stage'] == 'word':
                game.set_word(g, uid, text)
            elif game.current(g) == uid:
                game.clue(g, uid, text, self.clock())
            elif g['stage'] == 'guess' and uid == g.get('guesser'):
                game.guess(g, uid, text)
            else:
                # No reply spam: bring the existing panel up to date.
                pass
        self.touch(gid)
        self.private_panel(gid, uid, force=True)
        if g['stage'] == 'dealing' and uid in g['players'] and uid not in g.get('delivered', []):
            g['delivered'].append(uid)
            g['delivery_errors'] = [x for x in g.get('delivery_errors', []) if x != uid]
            if set(g['delivered']) == set(g['players']):
                game.dealt(g, self.clock())
            self.touch(gid)

    def message(self, m):
        chat, user = m['chat'], m.get('from', {})
        cid = chat['id']
        if m.get('migrate_to_chat_id'):
            new = m['migrate_to_chat_id']
            if cid in self.store.games:
                self.store.games[new] = self.store.games.pop(cid)
                self.store.games[new].pop('message_id', None)
            self.store.data['boards'].pop(cid, None)
            for uid, gid in self.store.data['pending'].items():
                if gid == cid:
                    self.store.data['pending'][uid] = new
            self.dirty.discard(cid)
            self.touch(new)
            return
        if not user or user.get('is_bot'):
            return
        text = (m.get('text') or '').strip()
        command = text.split(maxsplit=1)[0].lower() if text.startswith('/') else ''
        if '@' in command:
            command, target = command.split('@', 1)
            if target != self.username.lower():
                return
        if chat['type'] == 'private':
            self.private_message(m, command, text)
        elif chat['type'] in ('group', 'supergroup'):
            added = any(u.get('username', '').lower() == self.username.lower() for u in m.get('new_chat_members', []))
            if command == '/newgame':
                self.create(cid, user, chat.get('title', ''))
            elif command == '/begin' and cid in self.store.games:
                self.act(cid, user['id'], 'begin')
            elif command in ('/stop', '/finish', '/cancel') and cid in self.store.games:
                self.act(cid, user['id'], 'abort')
                self.private_panel(cid, user['id'], force=True)
            elif command in ('/start', '/help', '/status', '/players', '/results') or added:
                self.group_panel(cid, force=True)
                if cid in self.store.games:
                    self.dirty.add(cid)
            elif cid in self.store.games and not command:
                g = self.store.games[cid]
                if (m.get('reply_to_message') or {}).get('message_id') == g.get('message_id') and game.current(g) == user['id']:
                    game.clue(g, user['id'], text, self.clock())
                    self.touch(cid)

    def handle(self, update):
        try:
            if 'callback_query' in update:
                self.callback(update['callback_query'])
            elif 'message' in update:
                self.message(update['message'])
            elif 'my_chat_member' in update:
                member = update['my_chat_member']
                if member['chat']['type'] in ('group', 'supergroup') and member['new_chat_member']['status'] in ('member', 'administrator'):
                    self.group_panel(member['chat']['id'], force=True)
        except game.GameError as exc:
            # Text-entry errors get one brief private reply. The group stays quiet.
            m = update.get('message', {})
            if m.get('chat', {}).get('type') == 'private':
                self.api.call('sendMessage', chat_id=m['chat']['id'], text=str(exc), disable_notification=True)
            logging.info('User action rejected: %s', exc)
        self.store.data['offset'] = update['update_id'] + 1
        self.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-state', action='store_true')
    args = parser.parse_args()
    store = Store(os.environ.get('STATE_FILE', 'data/state.json'))
    if args.check_state:
        print(f'State compatible: {len(store.games)} games. No files changed.')
        return
    token = os.environ.get('BOT_TOKEN', '')
    if not token:
        raise SystemExit('Set BOT_TOKEN in /etc/imposter-bot.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    api = Telegram(token)
    me = api.call('getMe')
    if api.call('getWebhookInfo').get('url'):
        raise SystemExit('This token has a webhook. Use a dedicated bot or remove its webhook first.')
    api.call('setMyCommands', commands=[{'command': 'start', 'description': 'Открыть меню игры'},
                                       {'command': 'status', 'description': 'Обновить табло'},
                                       {'command': 'help', 'description': 'Правила и помощь'}])
    api.call('setChatMenuButton', menu_button={'type': 'commands'})
    bot = Bot(store, api, me['username'])
    logging.info('Bot started: @%s, saved games: %s', me['username'], len(store.games))
    while True:
        try:
            bot.timers()
            bot.flush()
            updates = api.call('getUpdates', offset=store.data['offset'], timeout=2,
                               allowed_updates=['message', 'callback_query', 'my_chat_member'])
            for update in updates:
                try:
                    bot.handle(update)
                except ApiError as exc:
                    logging.warning('Telegram update failed; will retry: %s', exc)
                    # Don't acknowledge this update if an essential network operation failed.
                    break
                except Exception:
                    logging.exception('Update failed')
                    store.data['offset'] = update['update_id'] + 1
                    store.save()
        except ApiError as exc:
            logging.warning('Telegram unavailable: %s', exc)
            time.sleep(min(max(exc.retry_after, 2), 10))
        except Exception:
            logging.exception('Main loop error')
            time.sleep(2)


if __name__ == '__main__':
    main()

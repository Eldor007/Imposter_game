"""Persistent game rules, independent of Telegram and wall-clock sleeps."""
import re
import secrets
import time
import unicodedata

MAX_PLAYERS = 50
TURN_SECONDS = 45
DISCUSS_SECONDS = 60
VOTE_SECONDS = 60
GUESS_SECONDS = 30
END_STAGES = {'finished', 'cancelled'}
LOBBY_STAGES = {'mode', 'count', 'word', 'ready'}
WORDS = ('такси', 'аэропорт', 'чемодан', 'самолёт', 'поезд', 'метро', 'бензин',
         'велосипед', 'мотоцикл', 'пицца', 'плов', 'суши', 'арбуз', 'мороженое',
         'кофе', 'чайник', 'холодильник', 'микроволновка', 'ресторан', 'рынок',
         'супермаркет', 'школа', 'университет', 'библиотека', 'больница', 'аптека',
         'стадион', 'футбол', 'баскетбол', 'бокс', 'шахматы', 'кинотеатр', 'попкорн',
         'гитара', 'микрофон', 'концерт', 'свадьба', 'день рождения', 'подарок',
         'телефон', 'наушники', 'компьютер', 'клавиатура', 'фотоаппарат', 'интернет',
         'пароль', 'робот', 'космос', 'луна', 'море', 'пляж', 'остров', 'пустыня',
         'горы', 'снег', 'дождь', 'зонт', 'радуга', 'молния', 'пожар', 'палатка',
         'компас', 'карта', 'лифт', 'зеркало', 'часы', 'кольцо', 'корона', 'пират',
         'сокровище', 'дракон', 'динозавр', 'акула', 'дельфин', 'пингвин', 'верблюд',
         'лев', 'тигр', 'кошка', 'собака', 'пчела', 'мёд', 'цветок', 'кактус',
         'ферма', 'трактор', 'молоток', 'магнит', 'батарейка', 'свеча', 'мыло')


class GameError(Exception):
    """A user-facing rejection; state remains unchanged."""


def now():
    return time.time()


def pname(player):
    return re.sub(r'\s+', ' ', str(player.get('first_name') or player.get('username') or player['id']))[:32]


def clean_word(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')).strip(' .!?\t\n')


def live(g):
    excluded = set(g.get('eliminated', []))
    return [uid for uid in g['players'] if uid not in excluded]


def event(g, text):
    g['history'] = (g.get('history', []) + [text])[-12:]


def phase(g, stage, seconds=0, at=None):
    g['stage'] = stage
    g['epoch'] = g.get('epoch', 0) + 1
    g['deadline'] = (now() if at is None else at) + seconds if seconds else 0


def owner(g, uid):
    if uid != g['admin']:
        raise GameError('Этой игрой управляет её создатель.')


def fresh(user, title='', previous=None, rematch=False):
    g = {'version': 2, 'nonce': secrets.token_urlsafe(9), 'admin': user['id'],
         'host_name': pname(user), 'group_title': title, 'players': {},
         'stage': 'mode', 'epoch': 1, 'deadline': 0, 'count': None, 'word': None,
         'mode': 'manual', 'panels': {}, 'eliminated': [], 'history': [], 'round': 0}
    g['lobby_expires'] = now() + 30 * 60
    if previous:
        g['message_id'] = previous.get('message_id')
        if rematch:
            owner(previous, user['id'])
            g['players'] = {k: dict(v) for k, v in previous['players'].items()}
            g['panels'] = dict(previous.get('panels', {}))
            g['mode'] = previous.get('mode', 'manual')
            g['count'] = previous.get('count') or 1
            if g['mode'] == 'manual':
                g['players'].pop(user['id'], None)
                g['stage'] = 'word'
            else:
                g['stage'] = 'ready'
    return g


def normalize(raw):
    """Upgrade v1 in place without rerolling roles, losing players or revealing words."""
    g = dict(raw)
    g['players'] = {int(k): v for k, v in g.get('players', {}).items()}
    for key in ('imposters', 'delivered', 'eliminated', 'order', 'candidates', 'ready_votes'):
        if key in g:
            g[key] = list(dict.fromkeys(int(x) for x in g[key]))
    for key in ('panels', 'votes', 'clues', 'views'):
        g[key] = {int(k): v for k, v in g.get(key, {}).items()}
    old = g.get('version', 1) < 2
    g.setdefault('mode', 'manual')
    g.setdefault('epoch', 1)
    g.setdefault('round', 1 if 'imposters' in g else 0)
    g.setdefault('eliminated', [])
    g.setdefault('history', [])
    g.setdefault('deadline', 0)
    g.setdefault('views', {})
    if old:
        mids = g.pop('host_panel_ids', [])
        if mids:
            g['panels'][g['admin']] = mids[-1]
        g.pop('status_ids', None)
        if g.get('stage') in ('playing', 'done'):
            phase(g, 'discussion', DISCUSS_SECONDS)
            event(g, 'Игра восстановлена. Обсудите ассоциации и переходите к голосованию.')
        elif 'imposters' in g and g.get('stage') == 'ready':
            phase(g, 'dealing')
        # Old final reveals weren't automatically adjudicated.
        if g.get('stage') == 'finished':
            g.setdefault('winner', 'stopped')
            g.setdefault('reason', 'Ведущий раскрыл роли.')
    g['version'] = 2
    return g


def choose_mode(g, uid, mode):
    owner(g, uid)
    if g['stage'] != 'mode' or mode not in ('manual', 'random'):
        raise GameError('Настройка уже изменена. Обнови панель.')
    g['mode'] = mode
    phase(g, 'count')


def set_count(g, uid, count):
    owner(g, uid)
    if g['stage'] != 'count' or not 1 <= count <= 20:
        raise GameError('Выбери число импостеров от 1 до 20.')
    g['count'] = count
    phase(g, 'word' if g['mode'] == 'manual' else 'ready')


def set_word(g, uid, text):
    owner(g, uid)
    if g['stage'] != 'word' or not clean_word(text) or len(text) > 100:
        raise GameError('Отправь слово или фразу до 100 символов.')
    g['word'] = text.strip()
    phase(g, 'ready')


def join(g, user):
    if g['stage'] not in LOBBY_STAGES:
        raise GameError('Раздача уже началась. Присоединяйся в следующем раунде.')
    uid = user['id']
    if uid == g['admin'] and g['mode'] != 'random':
        raise GameError('Ты ведущий со своим словом. В этом режиме ты наблюдаешь за игрой.')
    if uid not in g['players'] and len(g['players']) >= MAX_PLAYERS:
        raise GameError('В игре уже 50 участников.')
    g['players'][uid] = {'id': uid, 'first_name': pname(user), 'username': user.get('username', '')}


def leave(g, uid):
    if g['stage'] not in LOBBY_STAGES:
        raise GameError('После раздачи выход из состава закрыт. Если игрок молчит, его ход пропустит таймер.')
    g['players'].pop(uid, None)


def prepare(g, uid):
    owner(g, uid)
    if g['stage'] == 'dealing':
        return
    if g['stage'] != 'ready':
        raise GameError('Сначала закончи настройку игры.')
    needed = max(3, 2 * g['count'] + 1)
    if len(g['players']) < needed:
        raise GameError(f"Нужно ещё {needed - len(g['players'])} игроков: для {g['count']} импостеров минимум {needed}.")
    if g['mode'] == 'random':
        g['word'] = secrets.choice(WORDS)
    g['imposters'] = secrets.SystemRandom().sample(list(g['players']), g['count'])
    g['delivered'] = []
    g['eliminated'] = []
    g['round'] = 0
    g['stalls'] = 0
    g['views'] = {}
    phase(g, 'dealing')


def next_round(g, at=None):
    g['round'] += 1
    g['order'] = secrets.SystemRandom().sample(live(g), len(live(g)))
    g['turn'] = 0
    g['clues'] = {}
    g['votes'] = {}
    g['ready_votes'] = []
    g['views'] = {}
    phase(g, 'clues', TURN_SECONDS, at)


def dealt(g, at=None):
    if g['stage'] != 'dealing' or set(g['delivered']) != set(g['players']):
        raise GameError('Не всем доставлены роли. Пусть игроки откроют бота, затем повтори раздачу.')
    next_round(g, at)


def current(g):
    order = g.get('order', [])
    return order[g['turn']] if g['stage'] == 'clues' and g.get('turn', 0) < len(order) else None


def clue(g, uid, text='', at=None):
    if current(g) != uid:
        raise GameError('Сейчас ход другого игрока. Очередь видна в табло.')
    if len(text) > 60:
        raise GameError('Ассоциация должна быть короткой: до 60 символов.')
    if text and clean_word(text) == clean_word(g['word']):
        raise GameError('Не называй само секретное слово — дай ассоциацию.')
    g['clues'][uid] = re.sub(r'\s+', ' ', text).strip() or '✓ сказал в группе'
    g['turn'] += 1
    if g['turn'] >= len(g['order']):
        phase(g, 'discussion', DISCUSS_SECONDS, at)
    else:
        phase(g, 'clues', TURN_SECONDS, at)


def start_vote(g, at=None, runoff=None):
    g['votes'] = {}
    g['views'] = {}
    g['candidates'] = list(runoff) if runoff else live(g)
    g['runoff'] = bool(runoff)
    phase(g, 'voting', VOTE_SECONDS, at)


def ready_vote(g, uid, at=None):
    if g['stage'] != 'discussion' or uid not in live(g):
        raise GameError('Эта кнопка доступна участникам во время обсуждения.')
    g['ready_votes'] = list(dict.fromkeys(g.get('ready_votes', []) + [uid]))
    if len(g['ready_votes']) > len(live(g)) // 2:
        start_vote(g, at)


def finish(g, winner, reason):
    g['winner'] = winner
    g['reason'] = reason
    phase(g, 'finished')
    g['views'] = {}


def no_elimination(g, note, at=None):
    event(g, note)
    g['stalls'] = g.get('stalls', 0) + 1
    if g['stalls'] >= 3:
        finish(g, 'imposters', 'Три голосования подряд никого не исключили — импостеры остались нераскрытыми.')
    else:
        next_round(g, at)


def close_vote(g, at=None):
    if g['stage'] != 'voting':
        raise GameError('Голосование уже завершилось.')
    totals = {}
    for target in g['votes'].values():
        totals[target] = totals.get(target, 0) + 1
    maximum = max(totals.values(), default=0)
    tied = [uid for uid, votes in totals.items() if votes == maximum]
    g['last_tally'] = totals
    if not tied or 0 in tied:
        no_elimination(g, 'Голосование: никто не исключён.', at)
    elif len(tied) > 1:
        if g.get('runoff'):
            no_elimination(g, 'Повторная ничья: никто не исключён.', at)
        else:
            event(g, 'Ничья. Переголосование между лидерами.')
            start_vote(g, at, tied)
    else:
        uid = tied[0]
        g['eliminated'].append(uid)
        g['stalls'] = 0
        evil = uid in g['imposters']
        event(g, f"{pname(g['players'][uid])} исключён — {'импостер' if evil else 'знал слово'}.")
        remaining = set(live(g)) & set(g['imposters'])
        if not remaining:
            g['guesser'] = uid
            g['views'] = {}
            phase(g, 'guess', GUESS_SECONDS, at)
        elif len(remaining) >= len(live(g)) - len(remaining):
            finish(g, 'imposters', 'Импостеров осталось не меньше, чем обычных игроков.')
        else:
            next_round(g, at)


def cast_vote(g, uid, target, at=None):
    if g['stage'] != 'voting' or uid not in live(g):
        raise GameError('Голосуют только оставшиеся игроки во время голосования.')
    if target == uid or target not in g['candidates'] + [0]:
        raise GameError('Выбери другого игрока из текущего голосования.')
    g['votes'][uid] = target
    if len(g['votes']) == len(live(g)):
        close_vote(g, at)


def guess(g, uid, text):
    if g['stage'] != 'guess' or uid != g.get('guesser'):
        raise GameError('Последний шанс сейчас у пойманного импостера.')
    if not text.strip() or len(text) > 100:
        raise GameError('Отправь одну версию слова до 100 символов.')
    g['guess'] = text.strip()
    if clean_word(text) == clean_word(g['word']):
        finish(g, 'imposters', 'Пойманный импостер угадал секретное слово!')
    else:
        finish(g, 'players', 'Все импостеры найдены, секретное слово не угадано.')


def advance(g, uid, at=None):
    owner(g, uid)
    if g['stage'] == 'clues':
        clue(g, current(g), at=at)
    elif g['stage'] == 'discussion':
        start_vote(g, at)
    else:
        raise GameError('Следующий этап включится автоматически.')


def tick(g, at=None):
    at = now() if at is None else at
    if g['stage'] in LOBBY_STAGES | {'dealing'} and g.get('lobby_expires') and at >= g['lobby_expires']:
        finish(g, 'stopped', 'Лобби закрыто: игра не началась за 30 минут.')
        return True
    if not g.get('deadline') or at < g['deadline']:
        return False
    # Resume one current phase after downtime, giving the next player a full turn.
    if g['stage'] == 'clues':
        clue(g, current(g), at=at)
    elif g['stage'] == 'discussion':
        start_vote(g, at)
    elif g['stage'] == 'voting':
        close_vote(g, at)
    elif g['stage'] == 'guess':
        finish(g, 'players', 'Все импостеры найдены. Время на угадывание слова истекло.')
    else:
        return False
    return True

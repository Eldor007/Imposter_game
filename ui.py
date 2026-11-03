"""Text and inline keyboards. No network calls or state mutations."""
import math
import game as rules

RULES = (
    '🎭 КАК ИГРАТЬ\n\n'
    '1. Вступи в лобби. Бот пришлёт роль в личку: обычные игроки знают одно слово, импостеры его не знают.\n\n'
    '2. По очереди давайте короткую ассоциацию. Напиши её боту в личку или скажи в группе и нажми «Ассоциацию сказал». На ход — 45 секунд.\n\n'
    '3. После круга — минута обсуждения. Затем каждый голосует в личке. Можно пропустить голос; до окончания его можно изменить.\n\n'
    '4. Игрок с наибольшим числом голосов выбывает, его роль раскрывается. При ничьей — одно переголосование. Повторная ничья или победа «Пропустить» никого не исключает.\n\n'
    '5. Найдите всех импостеров! У последнего пойманного есть 30 секунд и одна попытка угадать слово. Верная попытка спасает команду импостеров.\n\n'
    '6. Если импостеров стало не меньше остальных или три голосования подряд никто не выбыл — побеждают импостеры.\n\n'
    'Создатель управляет игрой без прав админа. «Играю со всеми» — бот сам выбирает слово. «Я ведущий» — создатель задаёт слово и не играет. Таймеры пропускают молчащих игроков.'
)


def button(text, callback):
    return {'text': text, 'callback_data': callback}


def url(text, link):
    return {'text': text, 'url': link}


def kb(rows):
    return {'inline_keyboard': rows}


def code(g, action, arg=None, phase=False):
    parts = [action, g['nonce']]
    if phase:
        parts.append(str(g['epoch']))
    if arg is not None:
        parts.append(str(arg))
    return ':'.join(parts)


def link(g, username, kind='play'):
    return f"https://t.me/{username}?start={kind}_{g['nonce']}"


def timer(g, at):
    if not g.get('deadline'):
        return ''
    seconds = max(0, math.ceil((g['deadline'] - at) / 15) * 15)
    return f' • ≤{seconds} сек.'


def stage_text(g, at):
    stage = g['stage']
    current = rules.current(g)
    status = {
        'mode': '⚙️ Создатель выбирает режим', 'count': '⚙️ Выбираем число импостеров',
        'word': '✍️ Ведущий задаёт слово', 'ready': '🟢 Лобби открыто',
        'dealing': '📨 Доставляем роли', 'discussion': '💬 Обсуждение',
        'voting': '🗳 Переголосование' if g.get('runoff') else '🗳 Голосование',
        'guess': '🎯 Последний шанс пойманного импостера',
        'finished': '🏁 Игра завершена', 'cancelled': 'Игра отменена',
    }.get(stage, '🎭 Игра')
    if stage == 'clues' and current:
        status = f"💭 Ход: {rules.pname(g['players'][current])}"
    return status + timer(g, at)


def result(g):
    winners = {'players': '🏆 Победили знатоки слова!', 'imposters': '🎭 Победили импостеры!', 'stopped': 'Игра завершена досрочно.'}
    text = winners.get(g.get('winner'), 'Игра завершена.') + '\n' + g.get('reason', '')
    if g.get('word') and 'imposters' in g:
        text += f"\n\n🔓 Слово: {g['word']}\n🎭 Импостеры: "
        text += ', '.join(rules.pname(g['players'][uid]) for uid in g['imposters'] if uid in g['players'])
    if g.get('guess'):
        text += f"\nПопытка: {g['guess']}"
    return text


def roster(g, full=False, page=0):
    players = list(g['players'])
    subset = players[page * 10:(page + 1) * 10] if full else players[:20]
    rows = []
    for uid in subset:
        marker = '⬜'
        if uid in g.get('eliminated', []):
            marker = '🎭' if uid in g.get('imposters', []) else '⚪'
        elif rules.current(g) == uid:
            marker = '▶️'
        elif uid in g.get('clues', {}):
            marker = '✅'
        label = rules.pname(g['players'][uid])
        clue = g.get('clues', {}).get(uid)
        if clue and g['stage'] in ('clues', 'discussion'):
            label += f' — {clue[:40]}'
        rows.append(f'{marker} {label}')
    if not full and len(players) > 20:
        rows.append(f'… ещё {len(players) - 20}. Весь состав — в личной панели.')
    return '\n'.join(rows) or 'Пока никого. Нажми «Участвую»!'


def group_panel(g, username, at):
    stage = g['stage']
    text = f"🎭 ИМПОСТЕР\nСоздатель: {g.get('host_name', 'ведущий')}\n{stage_text(g, at)}"
    if g.get('count'):
        text += f"\nИгроков: {len(g['players'])} • Импостеров: {g['count']}"
    if stage in rules.LOBBY_STAGES and g.get('count'):
        needed = max(3, 2 * g['count'] + 1)
        missing = needed - len(g['players'])
        text += '\n✅ Можно начинать' if missing <= 0 else f'\nДо старта ещё {missing}'
    if g.get('round'):
        text += f"\nКруг {g['round']} • В игре: {len(rules.live(g))}"
    if stage == 'dealing':
        text += f"\nРоли доставлены: {len(g.get('delivered', []))}/{len(g['players'])}"
        failures = g.get('delivery_errors', [])
        if failures:
            text += '\nОткройте личку с ботом: ' + ', '.join(rules.pname(g['players'][uid]) for uid in failures)
    if stage == 'voting':
        text += f"\nПроголосовали: {len(g.get('votes', {}))}/{len(rules.live(g))}. Выбор скрыт."
    if stage == 'guess':
        uid = g.get('guesser')
        text += f"\n{rules.pname(g['players'][uid])}, отправь боту одну версию слова."
    if stage in rules.END_STAGES:
        text += '\n\n' + result(g)
    else:
        if g.get('history'):
            text += '\n\n' + '\n'.join(g['history'][-2:])
        text += '\n\n' + roster(g)
    rows = []
    if stage in rules.LOBBY_STAGES:
        rows += [[url('🎮 Участвую', link(g, username, 'join'))],
                 [url('⚙️ Создателю: настройка', link(g, username, 'setup'))]]
        if stage == 'ready':
            rows.append([button('🚀 Начать игру', code(g, 'begin'))])
        rows.append([button('Выйти', code(g, 'leave'))])
    elif stage == 'dealing':
        rows += [[url('📩 Открыть мою роль', link(g, username))], [button('🔁 Повторить доставку', code(g, 'begin'))]]
    elif stage == 'clues':
        text += '\n\nДай ассоциацию в личке или скажи её здесь и нажми кнопку.'
        rows += [[button('✅ Ассоциацию сказал', code(g, 'said', phase=True))], [url('📩 Моя роль и ход', link(g, username))]]
    elif stage == 'discussion':
        rows += [[button('🗳 Готов голосовать', code(g, 'ready', phase=True))], [url('📩 Личная панель', link(g, username))]]
        text += f"\n\nГотовы голосовать: {len(g.get('ready_votes', []))}/{len(rules.live(g))}. Большинство запускает голосование."
    elif stage == 'voting':
        rows.append([url('🗳 Проголосовать в личке', link(g, username, 'vote'))])
    elif stage == 'guess':
        rows.append([url('🎯 Открыть личку', link(g, username))])
    elif stage in rules.END_STAGES:
        rows += [[button('🔁 Реванш тем же составом', code(g, 'rematch'))], [button('🎲 Новая игра', 'newgame')]]
    rows.append([button('🔄 Обновить', code(g, 'status')), url('📖 Правила', f'https://t.me/{username}?start=rules')])
    return text[:4000], kb(rows)


def private_panel(g, uid, username, at):
    stage = g['stage']
    is_host = uid == g['admin']
    view = g.get('views', {}).get(uid, '')
    text = f"🎭 {g.get('group_title') or 'Твоя игра'}\n{stage_text(g, at)}"
    if stage not in rules.LOBBY_STAGES and stage not in rules.END_STAGES:
        text += f"\nВ игре: {len(rules.live(g))}/{len(g['players'])} • Круг {g.get('round', 0)}"
    if stage == 'voting':
        text += f"\nПроголосовали: {len(g.get('votes', {}))}/{len(rules.live(g))}."
    rows = []
    if view == 'rules':
        return RULES, kb([[button('← К игре', code(g, 'panel'))]])
    if view == 'abort' and is_host:
        return 'Завершить игру досрочно?\nЕсли роли уже назначены, слово и импостеры будут раскрыты. Победа никому не засчитается.', kb([[button('Да, завершить', code(g, 'confirm'))], [button('← Продолжить игру', code(g, 'panel'))]])
    if stage in rules.END_STAGES:
        text += '\n\n' + result(g)
        if is_host:
            rows.append([button('🔁 Реванш', code(g, 'rematch'))])
    elif is_host and stage == 'mode':
        text += '\n\nКак хочешь играть?\n🎲 Играю со всеми — бот выберет слово, ты тоже получишь случайную роль.\n✍️ Я ведущий — сам задашь слово и будешь наблюдать.'
        rows += [[button('🎲 Играю со всеми', code(g, 'mode', 'random', True))], [button('✍️ Я ведущий, своё слово', code(g, 'mode', 'manual', True))]]
    elif is_host and stage == 'count':
        text += '\n\nСколько импостеров? Для 1 нужно минимум 3 игрока, для 2 — 5, для 3 — 7. Можно отправить другое число до 20.'
        rows.append([button(str(n), code(g, 'count', n, True)) for n in (1, 2, 3)])
    elif is_host and stage == 'word':
        text += '\n\nОтправь сюда секретное слово или короткую фразу. Ты ведущий и не участвуешь в раздаче.'
    else:
        if uid in g['players'] and 'imposters' in g:
            if uid in g['imposters']:
                text += '\n\n🎭 ТЫ ИМПОСТЕР\nТы не знаешь слово. Подбирай ассоциации по подсказкам остальных.'
                partners = [rules.pname(g['players'][p]) for p in g['imposters'] if p != uid]
                if partners:
                    text += '\nНапарники: ' + ', '.join(partners)
            else:
                text += f"\n\n🔒 ТВОЁ СЛОВО: {g['word']}\nДавай ассоциации, не называя само слово."
            if uid in g.get('eliminated', []):
                text += '\nТы выбыл: наблюдай за игрой, больше голосовать нельзя.'
        elif is_host and g['mode'] == 'manual':
            text += '\n\nТы ведущий: управляешь игрой и не голосуешь.'
        elif uid not in g['players']:
            text += '\n\nТы пока не участвуешь.'
        if stage in rules.LOBBY_STAGES:
            if uid not in g['players'] and not (is_host and g['mode'] != 'random'):
                rows.append([button('🎮 Участвую', code(g, 'join'))])
            if uid in g['players']:
                rows.append([button('Выйти из лобби', code(g, 'leave'))])
            text += f"\n\nИгроков: {len(g['players'])} • Импостеров: {g.get('count') or 'выбираем'}\n\n" + roster(g)
            if is_host and stage == 'ready':
                rows.insert(0, [button('🚀 Начать игру', code(g, 'begin'))])
        elif stage == 'dealing':
            text += f"\n\nДоставлено ролей: {len(g.get('delivered', []))}/{len(g['players'])}."
            if is_host:
                rows.append([button('🔁 Повторить доставку', code(g, 'begin'))])
        elif stage == 'clues':
            actor = rules.current(g)
            if actor == uid:
                text += '\n\n▶️ ТВОЙ ХОД! Напиши сюда ассоциацию до 60 символов. Или скажи её в группе и нажми кнопку.'
                rows.append([button('✅ Ассоциацию сказал', code(g, 'said', phase=True))])
            else:
                text += f"\n\nСейчас ходит {rules.pname(g['players'][actor])}."
            if is_host:
                rows.append([button('⏭ Пропустить этот ход', code(g, 'advance', phase=True))])
        elif stage == 'discussion':
            text += '\n\nОбсудите ассоциации в группе. Затем выберите подозреваемого.'
            if uid in rules.live(g):
                rows.append([button('🗳 Готов голосовать', code(g, 'ready', phase=True))])
            if is_host:
                rows.append([button('Начать голосование сейчас', code(g, 'advance', phase=True))])
        elif stage == 'voting':
            if uid in rules.live(g):
                if view.startswith('ballot'):
                    page = int(view.partition(':')[2] or 0)
                    candidates = [x for x in g['candidates'] if x != uid]
                    last = max(0, (len(candidates) - 1) // 8)
                    page = max(0, min(page, last))
                    text += '\n\nКого исключаем? Выбор виден только тебе. Изменить можно до окончания голосования.'
                    for target in candidates[page * 8:(page + 1) * 8]:
                        label = rules.pname(g['players'][target])
                        if g.get('votes', {}).get(uid) == target:
                            label = '✅ ' + label
                        rows.append([button(label, code(g, 'vote', target, True))])
                    nav = []
                    if page:
                        nav.append(button('←', code(g, 'vp', page - 1, True)))
                    if page < last:
                        nav.append(button('→', code(g, 'vp', page + 1, True)))
                    if nav:
                        rows.append(nav)
                    rows.append([button('Пропустить голос', code(g, 'vote', 0, True))])
                else:
                    target = g.get('votes', {}).get(uid)
                    choice = 'пропустить' if target == 0 else rules.pname(g['players'][target]) if target else 'ещё не выбран'
                    text += f"\n\nТвой голос: {choice}.\nПроголосовали: {len(g.get('votes', {}))}/{len(rules.live(g))}."
                    rows.append([button('🗳 Выбрать / изменить голос', code(g, 'ballot', phase=True))])
            else:
                text += '\n\nГолосуют оставшиеся игроки. Ждём итогов.'
        elif stage == 'guess':
            text += '\n\n🎯 Отправь сюда ОДНУ версию секретного слова. У тебя 30 секунд!' if uid == g.get('guesser') else '\n\nПоследний пойманный импостер пытается угадать слово. Не подсказывайте!'
    if view.startswith('roster'):
        page = int(view.partition(':')[2] or 0)
        text = f"👥 Участники • {len(g['players'])}\n\n" + roster(g, True, page)
        nav = []
        if page:
            nav.append(button('←', code(g, 'roster', page - 1)))
        if (page + 1) * 10 < len(g['players']):
            nav.append(button('→', code(g, 'roster', page + 1)))
        rows = [nav] if nav else []
        rows.append([button('← К игре', code(g, 'panel'))])
    elif stage not in ('mode', 'count', 'word'):
        rows.append([button('👥 Участники', code(g, 'roster', 0)), button('🔄 Обновить', code(g, 'status'))])
    if is_host and stage not in rules.END_STAGES:
        rows.append([button('Завершить досрочно', code(g, 'abort'))])
    rows.append([button('📖 Правила', code(g, 'rules')), button('☰ Мои игры', 'home')])
    return text[:4000], kb(rows)

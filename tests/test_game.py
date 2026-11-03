import copy
import json
import tempfile
import unittest
from pathlib import Path

import bot
import game
import ui
from storage import Store


def user(uid):
    return {'id': uid, 'first_name': f'Игрок {uid}'}


def ready_game(count=1, players=5, mode='manual'):
    g = game.fresh(user(99), 'Друзья')
    game.choose_mode(g, 99, mode)
    game.set_count(g, 99, count)
    if mode == 'manual':
        game.set_word(g, 99, 'такси')
    for uid in range(1, players + 1):
        game.join(g, user(uid))
    return g


def playing_game(count=1, players=5):
    g = ready_game(count, players)
    game.prepare(g, 99)
    g['imposters'] = list(range(1, count + 1))
    g['delivered'] = list(g['players'])
    game.dealt(g, 1000)
    return g


class RuleTests(unittest.TestCase):
    def test_setup_modes_and_balance(self):
        g = ready_game()
        with self.assertRaises(game.GameError):
            game.join(g, user(99))
        g = ready_game(2, 4, mode='random')
        game.join(g, user(99))
        game.prepare(g, 99)
        self.assertEqual(len(g['imposters']), 2)
        self.assertIn(g['word'], game.WORDS)
        self.assertIn(99, g['players'])
        with self.assertRaises(game.GameError):
            game.prepare(ready_game(2, 4), 99)

    def test_retry_does_not_reroll_or_allow_join(self):
        g = ready_game()
        game.prepare(g, 99)
        original = copy.deepcopy(g)
        game.prepare(g, 99)
        self.assertEqual(g['imposters'], original['imposters'])
        with self.assertRaises(game.GameError):
            game.join(g, user(8))
        with self.assertRaises(game.GameError):
            game.dealt(g)

    def test_turns_then_discussion_then_voting(self):
        g = playing_game()
        for uid in list(g['order']):
            self.assertEqual(game.current(g), uid)
            game.clue(g, uid, 'машина', 1000)
        self.assertEqual(g['stage'], 'discussion')
        game.ready_vote(g, 1, 1000)
        game.ready_vote(g, 2, 1000)
        self.assertEqual(g['stage'], 'discussion')
        game.ready_vote(g, 3, 1000)
        self.assertEqual(g['stage'], 'voting')

    def test_clue_validation_and_timeouts(self):
        g = playing_game()
        actor = game.current(g)
        with self.assertRaises(game.GameError):
            game.clue(g, 999, 'топливо')
        with self.assertRaises(game.GameError):
            game.clue(g, actor, 'ТАКСИ!')
        with self.assertRaises(game.GameError):
            game.clue(g, actor, 'a' * 61)
        game.tick(g, g['deadline'])
        self.assertNotEqual(game.current(g), actor)
        self.assertIn(actor, g['clues'])
        while g['stage'] == 'clues':
            game.tick(g, g['deadline'])
        game.tick(g, g['deadline'])
        self.assertEqual(g['stage'], 'voting')

    def test_vote_membership_self_vote_change_and_private_role(self):
        g = playing_game()
        game.start_vote(g, 1000)
        for voter, target in [(999, 1), (1, 1), (1, 999)]:
            with self.assertRaises(game.GameError):
                game.cast_vote(g, voter, target)
        game.cast_vote(g, 2, 1)
        game.cast_vote(g, 2, 3)
        self.assertEqual(g['votes'], {2: 3})
        self.assertNotIn('такси', ui.group_panel(g, 'bot', 1000)[0])
        self.assertNotIn('ТВОЁ СЛОВО', ui.private_panel(g, 1, 'bot', 1000)[0])
        self.assertIn('такси', ui.private_panel(g, 2, 'bot', 1000)[0])

    def test_single_imposter_caught_and_wrong_guess(self):
        g = playing_game(players=3)
        game.start_vote(g, 1000)
        for voter, target in [(1, 2), (2, 1), (3, 1)]:
            game.cast_vote(g, voter, target, 1000)
        self.assertEqual(g['stage'], 'guess')
        self.assertNotIn('такси', ui.group_panel(g, 'bot', 1000)[0])
        with self.assertRaises(game.GameError):
            game.guess(g, 2, 'такси')
        game.guess(g, 1, 'автобус')
        self.assertEqual(g['winner'], 'players')
        self.assertIn('такси', ui.group_panel(g, 'bot', 1000)[0])

    def test_correct_guess_or_timeout(self):
        for answer in ('  ТАКСИ! ', None):
            g = playing_game(players=3)
            game.start_vote(g, 1000)
            g['votes'] = {2: 1, 3: 1}
            game.close_vote(g, 1000)
            if answer:
                game.guess(g, 1, answer)
            else:
                game.tick(g, g['deadline'])
            self.assertEqual(g['winner'], 'imposters' if answer else 'players')

    def test_multiple_imposters_complete_game(self):
        g = playing_game(2, 5)
        game.start_vote(g, 1000)
        g['votes'] = {3: 1, 4: 1, 5: 1}
        game.close_vote(g, 1000)
        self.assertEqual(g['stage'], 'clues')
        self.assertEqual(g['round'], 2)
        self.assertNotIn(1, game.live(g))
        game.start_vote(g, 2000)
        with self.assertRaises(game.GameError):
            game.cast_vote(g, 1, 2)
        g['votes'] = {3: 2, 4: 2, 5: 2}
        game.close_vote(g, 2000)
        self.assertEqual(g['guesser'], 2)
        game.guess(g, 2, 'не знаю')
        self.assertEqual(g['winner'], 'players')

    def test_parity_ends_game(self):
        g = playing_game(2, 5)
        game.start_vote(g, 1000)
        g['votes'] = {1: 5, 2: 5, 3: 5}
        game.close_vote(g, 1000)
        self.assertEqual(g['winner'], 'imposters')

    def test_runoff_repeated_tie_and_skip(self):
        g = playing_game()
        game.start_vote(g, 1000)
        g['votes'] = {1: 2, 2: 1}
        game.close_vote(g, 1000)
        self.assertTrue(g['runoff'])
        self.assertEqual(set(g['candidates']), {1, 2})
        g['votes'] = {1: 2, 2: 1}
        game.close_vote(g, 1000)
        self.assertEqual(g['stage'], 'clues')
        self.assertFalse(g['eliminated'])
        for _ in range(2):
            game.start_vote(g, 1000)
            g['votes'] = {1: 0, 2: 0}
            game.close_vote(g, 1000)
        self.assertEqual(g['winner'], 'imposters')

    def test_no_vote_timeout_is_abstention(self):
        g = playing_game()
        game.start_vote(g, 1000)
        game.tick(g, g['deadline'])
        self.assertEqual(g['round'], 2)
        self.assertFalse(g['eliminated'])

    def test_all_callbacks_fit_telegram_limit_and_text_size(self):
        g = ready_game(players=50)
        for p in g['players'].values():
            p['first_name'] = '😀' * 32
        game.prepare(g, 99)
        g['delivered'] = list(g['players'])
        game.dealt(g, 1000)
        for stage in ('clues', 'discussion', 'voting', 'guess', 'finished'):
            g['stage'] = stage
            g.update(candidates=list(g['players']), guesser=1)
            for uid in (1, 2, 99):
                for text, keyboard in (ui.group_panel(g, 'bot', 1000), ui.private_panel(g, uid, 'bot', 1000)):
                    self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
                    for row in keyboard['inline_keyboard']:
                        for b in row:
                            if 'callback_data' in b:
                                self.assertLessEqual(len(b['callback_data'].encode()), 64)


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.messages = {}
        self.blocked = set()
        self.next_id = 100

    def call(self, method, **params):
        self.calls.append((method, copy.deepcopy(params)))
        if params.get('chat_id') in self.blocked:
            raise bot.ApiError('Forbidden: bot blocked', 403)
        if method == 'sendMessage':
            self.next_id += 1
            self.messages[params['chat_id'], self.next_id] = copy.deepcopy(params)
            return {'message_id': self.next_id}
        if method == 'editMessageText':
            key = params['chat_id'], params['message_id']
            if key not in self.messages:
                raise bot.ApiError('Bad Request: message to edit not found', 400)
            self.messages[key].update(copy.deepcopy(params))
        return True


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.store = Store(self.path)
        self.api = FakeTelegram()
        self.at = 1000000
        self.bot = bot.Bot(self.store, self.api, 'test_bot', lambda: self.at)

    def init(self, count=1, players=5):
        g = ready_game(count, players)
        self.store.games[-1] = g
        self.bot.focus(99, -1)
        self.bot.touch(-1)
        self.bot.group_panel(-1, True)
        self.bot.private_panel(-1, 99, True)
        return g

    def press(self, uid, action, arg=None, private=False, phase=False):
        g = self.store.games[-1]
        data = ui.code(g, action, arg, phase)
        chat = {'id': uid if private else -1, 'type': 'private' if private else 'supergroup'}
        mid = g['panels'].get(uid) if private else g['message_id']
        self.bot.callback({'id': 'q', 'from': user(uid), 'data': data,
                           'message': {'chat': chat, 'message_id': mid}})
        self.at += 5
        self.bot.flush()

    def test_status_and_joins_edit_existing_group_and_private_board(self):
        g = self.init()
        mid = g['message_id']
        host_mid = g['panels'][99]
        self.api.calls.clear()
        for uid in (6, 7):
            self.bot.private_message({'chat': {'id': uid, 'type': 'private'}, 'from': user(uid)}, '/start', '/start join_' + g['nonce'])
        self.at += 5
        self.bot.flush()
        self.bot.message({'chat': {'id': -1, 'type': 'supergroup'}, 'from': user(1), 'text': '/status'})
        self.assertEqual(g['message_id'], mid)
        self.assertEqual(g['panels'][99], host_mid)
        self.assertIn('Игроков: 7', self.api.messages[-1, mid]['text'])
        self.assertIn('Игрок 7', self.api.messages[99, host_mid]['text'])
        self.assertFalse(any(m == 'sendMessage' and p['chat_id'] in (-1, 99) for m, p in self.api.calls))

    def test_only_creator_controls_and_stale_vote_buttons_rejected(self):
        g = self.init()
        self.press(2, 'begin')
        self.assertNotIn('imposters', g)
        self.press(99, 'begin')
        self.assertEqual(g['stage'], 'clues')
        old = ui.code(g, 'said', phase=True)
        game.clue(g, game.current(g), at=self.at)
        self.bot.callback({'id': 'q', 'from': user(game.current(g)), 'data': old,
                           'message': {'chat': {'id': -1, 'type': 'supergroup'}}})
        self.assertTrue(self.api.calls[-1][1]['show_alert'])

    def test_delivery_failure_retry_preserves_roles_and_does_not_spam(self):
        g = self.init()
        self.api.blocked.add(3)
        self.press(99, 'begin')
        self.assertEqual(g['stage'], 'dealing')
        original = list(g['imposters'])
        ids = dict(g['panels'])
        self.api.blocked.remove(3)
        self.at += 130
        self.press(99, 'begin')
        self.assertEqual(g['stage'], 'clues')
        self.assertEqual(g['imposters'], original)
        self.assertTrue(all(g['panels'][uid] == mid for uid, mid in ids.items()))

    def test_large_delivery_batches_make_progress(self):
        g = self.init(players=20)
        self.press(99, 'begin')
        for _ in range(5):
            self.at += 5
            self.bot.timers()
        self.assertEqual(set(g['delivered']), set(g['players']))
        self.assertEqual(g['stage'], 'clues')

    def test_manual_finish_requires_confirmation_and_no_score(self):
        g = self.init()
        self.press(99, 'begin')
        self.press(99, 'abort', private=True)
        self.assertNotIn(g['stage'], game.END_STAGES)
        self.press(2, 'confirm')
        self.assertNotIn(g['stage'], game.END_STAGES)
        self.press(99, 'confirm', private=True)
        self.assertEqual(g['winner'], 'stopped')
        self.assertFalse(self.store.data['stats'])

    def test_result_scores_once_and_rematch_reuses_board_and_panels(self):
        g = self.init()
        self.press(99, 'begin')
        mids = dict(g['panels'])
        group_mid = g['message_id']
        game.finish(g, 'players', 'Все найдены')
        self.bot.score(g)
        self.bot.score(g)
        self.assertEqual(self.store.data['stats'][2]['games'], 1)
        self.press(99, 'rematch', private=True)
        new = self.store.games[-1]
        self.assertEqual(new['stage'], 'word')
        self.assertEqual(new['message_id'], group_mid)
        self.assertEqual(new['panels'], mids)
        self.assertNotIn('imposters', new)
        self.assertEqual(set(new['players']), {1, 2, 3, 4, 5})

    def test_restart_keeps_votes_deadline_panels_and_roles(self):
        g = self.init()
        self.press(99, 'begin')
        game.start_vote(g, self.at)
        game.cast_vote(g, 2, 1)
        self.store.save()
        loaded = Store(self.path).games[-1]
        for key in ('players', 'imposters', 'delivered', 'votes', 'deadline', 'panels'):
            self.assertEqual(loaded[key], g[key])

    def test_migrate_deployed_v1_keeps_role_word_and_live_host_panel(self):
        legacy = {'games': {'-1': {'admin': 99, 'nonce': 'old', 'players': {'1': user(1), '2': user(2), '3': user(3)},
                   'stage': 'playing', 'count': 1, 'word': 'такси', 'imposters': [1], 'delivered': [1, 2, 3],
                   'message_id': 100, 'host_panel_ids': [101, 102]}}, 'pending': {'99': -1}, 'offset': 30}
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        store = Store(self.path)
        g = store.games[-1]
        self.assertEqual(g['stage'], 'discussion')
        self.assertEqual(g['imposters'], [1])
        self.assertEqual(g['word'], 'такси')
        self.assertEqual(g['panels'][99], 102)
        self.assertEqual(g['message_id'], 100)
        self.assertEqual(store.data['offset'], 30)
        self.assertEqual(self.path.read_text(encoding='utf-8'), json.dumps(legacy))

    def test_deleted_board_recreated_once_then_edited(self):
        g = self.init()
        self.api.messages.pop((-1, g['message_id']))
        self.bot.cache.clear()
        self.bot.group_panel(-1, True)
        mid = g['message_id']
        self.at += 5
        self.bot.group_panel(-1, True)
        self.assertEqual(mid, g['message_id'])

    def test_role_delivery_clears_roster_view_before_marking_delivered(self):
        g = self.init()
        g['views'] = {2: 'roster:0'}
        self.press(99, 'begin')
        text = self.api.messages[2, g['panels'][2]]['text']
        self.assertTrue('ТЫ ИМПОСТЕР' in text or 'ТВОЁ СЛОВО' in text)
        self.assertIn(2, g['delivered'])

    def test_complete_button_flow_from_menu_to_win_and_rematch(self):
        self.bot.message({'chat': {'id': -1, 'type': 'supergroup', 'title': 'Друзья'}, 'from': user(99), 'text': '/start'})
        self.bot.callback({'id': 'new', 'data': 'newgame', 'from': user(99),
                           'message': {'chat': {'id': -1, 'type': 'supergroup', 'title': 'Друзья'}}})
        g = self.store.games[-1]
        self.bot.private_message({'chat': {'id': 99, 'type': 'private'}, 'from': user(99)}, '/start', '/start setup_' + g['nonce'])
        self.press(99, 'mode', 'random', private=True, phase=True)
        self.press(99, 'count', 1, private=True, phase=True)
        self.assertEqual(g['stage'], 'ready')
        for uid in (1, 2):
            self.bot.private_message({'chat': {'id': uid, 'type': 'private'}, 'from': user(uid)}, '/start', '/start join_' + g['nonce'])
        self.press(99, 'begin', private=True)
        evil = g['imposters'][0]
        while g['stage'] == 'clues':
            actor = game.current(g)
            self.bot.private_message({'chat': {'id': actor, 'type': 'private'}, 'from': user(actor)}, '', 'намёк')
            self.at += 5
        for uid in list(g['players'])[:2]:
            self.press(uid, 'ready', phase=True)
        self.assertEqual(g['stage'], 'voting')
        for uid in list(g['players']):
            self.press(uid, 'ballot', private=True, phase=True)
            target = evil if uid != evil else next(p for p in g['players'] if p != evil)
            self.press(uid, 'vote', target, private=True, phase=True)
        self.assertEqual(g['stage'], 'guess')
        self.bot.private_message({'chat': {'id': evil, 'type': 'private'}, 'from': user(evil)}, '', 'не знаю')
        self.at += 5
        self.bot.flush()
        self.assertEqual(g['winner'], 'players')
        self.assertTrue(g['scored'])
        group_sends = [p for m, p in self.api.calls if m == 'sendMessage' and p['chat_id'] == -1]
        self.assertEqual(len(group_sends), 1)
        for uid in g['players']:
            self.assertEqual(len([p for m, p in self.api.calls if m == 'sendMessage' and p['chat_id'] == uid]), 1)
        self.press(99, 'rematch', private=True)
        self.assertEqual(self.store.games[-1]['stage'], 'ready')
        self.assertEqual(set(self.store.games[-1]['players']), {99, 1, 2})

    def test_home_selection_does_not_alias_personal_panel(self):
        g = self.init()
        role_mid = g['panels'][99]
        self.bot.home(99)
        home_mid = self.store.data['homes'][99]
        self.bot.callback({'id': 'q', 'data': ui.code(g, 'open'), 'from': user(99),
                           'message': {'chat': {'id': 99, 'type': 'private'}, 'message_id': home_mid}})
        self.assertEqual(g['panels'][99], role_mid)
        self.assertNotEqual(role_mid, home_mid)

    def test_scores_survive_immediate_next_game_before_flush(self):
        g = self.init()
        self.press(99, 'begin')
        game.finish(g, 'players', 'Готово')
        self.bot.create(-1, user(4))
        self.assertEqual(self.store.data['stats'][1]['games'], 1)

    def test_expired_unstarted_lobby_cancels_without_roles(self):
        g = self.init()
        self.at = g['lobby_expires'] + 1
        self.bot.timers()
        self.assertEqual(g['winner'], 'stopped')
        self.assertNotIn('imposters', g)

    def test_personal_panel_unblocks_on_incoming_start(self):
        g = self.init()
        self.bot.cooldown[99, g['panels'][99]] = self.at + 120
        self.bot.private_message({'chat': {'id': 99, 'type': 'private'}, 'from': user(99)}, '/start', '/start')
        self.assertFalse(any(key[0] == 99 for key in self.bot.cooldown))


if __name__ == '__main__':
    unittest.main()

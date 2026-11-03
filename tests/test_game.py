import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bot


class GameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        bot.STATE = Path(self.tmp.name) / 'state.json'
        bot.games = {-1: {'admin': 99, 'nonce': 'testnonce', 'players': {i: {'id': i, 'first_name': str(i)} for i in range(1, 7)}, 'count': 2, 'word': 'такси', 'stage': 'ready'}}
        bot.pending = {99: -1}
        bot.offset = 123

    def test_exact_count_and_no_word_for_imposters(self):
        messages = []
        with patch.object(bot, 'send', side_effect=lambda cid, text, **kw: messages.append((cid, text))), patch.object(bot.time, 'sleep'):
            bot.start_game(-1)
        roles = [(cid, text) for cid, text in messages if cid > 0]
        self.assertEqual(len(roles), 6)
        self.assertEqual(sum('ИМПОСТЕР' in text for _, text in roles), 2)
        self.assertEqual(sum('такси' in text for _, text in roles), 4)

    def test_failed_delivery_retry_keeps_roles(self):
        def send(cid, text, **kw):
            if cid == 3:
                raise RuntimeError('blocked')
        with patch.object(bot, 'send', side_effect=send), patch.object(bot.time, 'sleep'):
            bot.start_game(-1)
        assigned = bot.games[-1]['imposters'].copy()
        bot.save()
        bot.load()
        self.assertEqual(bot.games[-1]['imposters'], assigned)
        self.assertEqual(bot.offset, 123)
        retried = []
        with patch.object(bot, 'send', side_effect=lambda cid, text, **kw: retried.append(cid)), patch.object(bot.time, 'sleep'):
            bot.start_game(-1)
        self.assertEqual([cid for cid in retried if cid > 0], [3])
        self.assertEqual(bot.games[-1]['stage'], 'playing')

    def test_too_few_players_no_assignment(self):
        bot.games[-1]['count'] = 6
        with patch.object(bot, 'send'):
            bot.start_game(-1)
        self.assertNotIn('imposters', bot.games[-1])

    def test_setup_and_join_from_telegram_links(self):
        bot.games = {}
        bot.pending = {}
        bot.USERNAME = 'test_bot'
        host = {'id': 99, 'first_name': 'Host'}
        messages = []
        def send(cid, text, **kw):
            messages.append((cid, text, kw))
            return {'message_id': len(messages)}
        with patch.object(bot, 'send', side_effect=send), patch.object(bot, 'refresh'):
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': host, 'text': '/newgame'})
            nonce = bot.games[-1]['nonce']
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': '/start setup_' + nonce})
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': '2'})
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': 'такси'})
            bot.on_message({'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1}, 'text': '/start join_' + nonce})
        self.assertEqual(bot.games[-1]['stage'], 'ready')
        self.assertIn(1, bot.games[-1]['players'])
        self.assertFalse(any('такси' in text for cid, text, kw in messages if cid == -1))

    def test_any_member_can_create_game(self):
        bot.games = {}
        with patch.object(bot, 'send', return_value={'message_id': 1}):
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 1}, 'text': '/newgame'})
        self.assertEqual(bot.games[-1]['admin'], 1)

    def test_only_creator_can_start_or_stop(self):
        with patch.object(bot, 'send'), patch.object(bot, 'start_game') as start:
            for cmd in ('/begin', '/stop'):
                bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 2}, 'text': cmd})
            start.assert_not_called()
            self.assertIn(-1, bot.games)
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 99}, 'text': '/begin'})
            start.assert_called_once_with(-1)

    def test_callback_owner_check_and_old_buttons(self):
        with patch.object(bot, 'api') as api, patch.object(bot, 'start_game') as start:
            bot.on_callback({'id': 'q', 'data': 'begin:testnonce', 'from': {'id': 2}})
            self.assertTrue(api.call_args.kwargs['show_alert'])
            start.assert_not_called()
            bot.on_callback({'id': 'q', 'data': 'count:oldnonce:3', 'from': {'id': 99}})
            self.assertEqual(bot.games[-1]['count'], 2)
            bot.on_callback({'id': 'q', 'data': 'begin:testnonce', 'from': {'id': 99}})
            start.assert_called_once_with(-1)

    def test_group_start_shows_new_game_button(self):
        with patch.object(bot, 'send') as send:
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 1}, 'text': '/start'})
        self.assertEqual(send.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'newgame')

    def test_creator_cannot_join_as_player(self):
        with patch.object(bot, 'send'):
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': {'id': 99}, 'text': '/start join_testnonce'})
        self.assertNotIn(99, bot.games[-1]['players'])

    def test_status_hides_secrets_until_host_finishes(self):
        game = bot.games[-1]
        game.update(stage='playing', imposters={1, 2}, delivered=set(range(1, 7)))
        self.assertNotIn('такси', bot.lobby_text(game))
        self.assertIn('6/6', bot.lobby_text(game))
        with patch.object(bot, 'send'), patch.object(bot, 'api'), patch.object(bot, 'refresh'):
            bot.on_callback({'id': 'q', 'data': 'finish:testnonce', 'from': {'id': 3}})
            self.assertEqual(game['stage'], 'playing')
            bot.on_callback({'id': 'q', 'data': 'finish:testnonce', 'from': {'id': 99}})
        self.assertEqual(game['stage'], 'finished')
        self.assertIn('такси', bot.lobby_text(game))
        self.assertIn('Импостеры: 1, 2', bot.lobby_text(game))
        bot.load()
        self.assertEqual(bot.games[-1]['stage'], 'finished')

    def test_game_cannot_be_replaced_before_results(self):
        bot.games[-1]['stage'] = 'playing'
        with patch.object(bot, 'send'):
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 3}, 'text': '/newgame'})
        self.assertEqual(bot.games[-1]['admin'], 99)

    def test_live_board_updates_on_leave(self):
        bot.games[-1]['message_id'] = 100
        bot.games[-1]['status_ids'] = [101]
        with patch.object(bot, 'api') as api:
            bot.on_callback({'id': 'q', 'data': 'leave:testnonce', 'from': {'id': 3}})
        edits = [c for c in api.call_args_list if c.args[0] == 'editMessageText']
        self.assertEqual(len(edits), 2)
        self.assertTrue(all('Игроки (5)' in c.kwargs['text'] for c in edits))

    def test_legacy_dealt_game_loads_as_playing(self):
        bot.games[-1]['stage'] = 'done'
        bot.save()
        bot.load()
        self.assertEqual(bot.games[-1]['stage'], 'playing')


if __name__ == '__main__':
    unittest.main()

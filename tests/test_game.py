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
        bot.games = {-1: {'admin': 99, 'players': {i: {'id': i, 'first_name': str(i)} for i in range(1, 7)}, 'count': 2, 'word': 'такси', 'stage': 'ready'}}
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
        self.assertEqual(bot.games[-1]['stage'], 'done')

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
        with patch.object(bot, 'send', side_effect=send), patch.object(bot, 'is_admin', return_value=True), patch.object(bot, 'refresh'):
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': host, 'text': '/newgame'})
            nonce = bot.games[-1]['nonce']
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': '/start setup_' + nonce})
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': '2'})
            bot.on_message({'chat': {'id': 99, 'type': 'private'}, 'from': host, 'text': 'такси'})
            bot.on_message({'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1}, 'text': '/start join_' + nonce})
        self.assertEqual(bot.games[-1]['stage'], 'ready')
        self.assertIn(1, bot.games[-1]['players'])
        self.assertFalse(any('такси' in text for cid, text, kw in messages if cid == -1))

    def test_non_admin_cannot_create_game(self):
        bot.games = {}
        with patch.object(bot, 'send'), patch.object(bot, 'is_admin', return_value=False):
            bot.on_message({'chat': {'id': -1, 'type': 'group'}, 'from': {'id': 1}, 'text': '/newgame'})
        self.assertFalse(bot.games)


if __name__ == '__main__':
    unittest.main()

"""Atomic JSON snapshots compatible with the first deployed version."""
import json
import os
import time
from pathlib import Path
import game


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {'schema': 2, 'games': {}, 'pending': {}, 'offset': None,
                     'homes': {}, 'boards': {}, 'stats': {}}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            self.data.update(raw)
            self.data['schema'] = 2
            self.data['games'] = {int(k): game.normalize(v) for k, v in raw.get('games', {}).items()}
            for key in ('pending', 'homes', 'boards', 'stats'):
                self.data[key] = {int(k): v for k, v in raw.get(key, {}).items()}
            for gid, g in self.data['games'].items():
                if g.get('message_id'):
                    self.data['boards'][gid] = g['message_id']

    @property
    def games(self):
        return self.data['games']

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, default=list)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(temp, self.path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)

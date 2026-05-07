"""Create and seed the SQLite database."""
import sqlite3
import random
import string
import hashlib
from datetime import datetime, timedelta

DB = 'twitter.db'

WORDS = [
    'hello', 'world', 'python', 'fastapi', 'sqlite', 'twitter', 'clone',
    'awesome', 'great', 'cool', 'fun', 'interesting', 'learning', 'code',
    'project', 'class', 'school', 'college', 'student', 'professor',
    'homework', 'assignment', 'deadline', 'coffee', 'sleep', 'tired',
    'excited', 'happy', 'sad', 'confused', 'motivated', 'focused',
    'distracted', 'procrastinating', 'studying', 'reading', 'writing',
    'debugging', 'testing', 'deploying', 'merging', 'pushing', 'pulling',
    'branching', 'committing', 'reviewing', 'refactoring', 'optimizing',
]

TEMPLATES = [
    'Just {verb} my {noun} and feeling {adj}!',
    'Anyone else {verb} at {time}? {emoji}',
    "Can't believe I'm still {verb} this {noun}",
    'Hot take: {noun} is actually {adj}',
    'Day {n} of {verb}: going {adj}',
    '{adj} day to be {verb} {noun}',
    'reminder that {noun} is {adj} and that\'s "okay"',
    "just wanted to say: {noun} is {adj}",
    'ngl {verb} this {noun} hits different at {time}',
    '{emoji} {noun} szn',
]

VERBS = ['debugging', 'writing', 'reading', 'building', 'shipping', 'learning',
         'pushing', 'studying', 'testing', 'deploying', 'reviewing', 'fixing']
NOUNS = ['code', 'project', 'PR', 'commit', 'feature', 'bug', 'test', 'app',
         'database', 'API', 'server', 'function', 'class', 'module', 'repo']
ADJS = ['fire', 'based', 'mid', 'goated', 'cooked', 'slay', 'valid', 'sus',
        'bussin', 'lowkey', 'highkey', 'clutch', 'cringe', 'based', 'vibes']
EMOJIS = ['🔥', '💯', '😭', '🤣', '✨', '🚀', '💀', '👀', '🤔', '😤', '🙏', '💪']
TIMES = ['3am', '2am', 'midnight', 'dawn', 'noon', 'dusk', '11pm', 'sunrise']


def random_message():
    tmpl = random.choice(TEMPLATES)
    return tmpl.format(
        verb=random.choice(VERBS),
        noun=random.choice(NOUNS),
        adj=random.choice(ADJS),
        emoji=random.choice(EMOJIS),
        time=random.choice(TIMES),
        n=random.randint(1, 100),
    )


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def create_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.executescript('''
        DROP TABLE IF EXISTS replies;
        DROP TABLE IF EXISTS messages_fts;
        DROP TABLE IF EXISTS messages;
        DROP TABLE IF EXISTS users;

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bio TEXT DEFAULT '',
            avatar_seed TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edited_at TIMESTAMP,
            parent_id INTEGER DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX idx_messages_created ON messages(created_at DESC);
        CREATE INDEX idx_messages_user ON messages(user_id);
        CREATE INDEX idx_messages_parent ON messages(parent_id);

        CREATE VIRTUAL TABLE messages_fts USING fts5(
            body,
            content=messages,
            content_rowid=id
        );

        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
        END;
        CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, body)
            VALUES ('delete', old.id, old.body);
        END;
        CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, body)
            VALUES ('delete', old.id, old.body);
            INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
        END;
    ''')

    # Seed two demo accounts with the required single/double quotes message
    demo_users = [
        ('demo', 'demo', "It's a beautiful day! He said \"hello\" to everyone."),
        ('admin', 'admin', 'Welcome to TweetBox — the best Twitter clone around!'),
    ]
    for username, pw, first_msg in demo_users:
        cur.execute(
            'INSERT OR IGNORE INTO users (username, password_hash, avatar_seed) VALUES (?,?,?)',
            (username, hash_password(pw), username),
        )
        cur.execute('SELECT id FROM users WHERE username=?', (username,))
        uid = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO messages (user_id, body, created_at) VALUES (?,?,?)',
            (uid, first_msg, datetime.now().isoformat()),
        )

    # Seed 200 random users, each with 200 messages (40 000 total)
    base_time = datetime.now() - timedelta(days=365)
    usernames = []
    for i in range(200):
        uname = f'user{i:03d}'
        pw_hash = hash_password(f'pass{i}')
        cur.execute(
            'INSERT OR IGNORE INTO users (username, password_hash, avatar_seed) VALUES (?,?,?)',
            (uname, pw_hash, uname),
        )
        usernames.append(uname)

    cur.execute('SELECT id, username FROM users WHERE username LIKE "user%"')
    uid_map = {row[1]: row[0] for row in cur.fetchall()}

    batch = []
    for uname in usernames:
        uid = uid_map.get(uname)
        if uid is None:
            continue
        for j in range(200):
            offset_secs = random.randint(0, 365 * 24 * 3600)
            ts = (base_time + timedelta(seconds=offset_secs)).isoformat()
            batch.append((uid, random_message(), ts))

    cur.executemany(
        'INSERT INTO messages (user_id, body, created_at) VALUES (?,?,?)',
        batch,
    )

    con.commit()
    con.close()
    print('Database created.')


if __name__ == '__main__':
    create_db()

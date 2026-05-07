# TweetBox

A Twitter clone built with FastAPI and SQLite3 for CSCI040.

## Setup

```bash
pip install -r requirements.txt
python db_create.py
uvicorn app:app --reload
```

Then open http://localhost:8000

Demo accounts: `demo/demo`, `admin/admin`

## Features

- Home feed with pagination (50 posts per page)
- Login / Logout / Sign up
- Create, edit, delete posts
- Threaded replies
- User profiles with bio and Robohash avatars
- Full-text search (SQLite FTS5)
- @mention linking
- URL auto-linking
- Markdown formatting in posts
- Change password
- Delete account
- JSON API: `/messages.json`
- 200 seed users × 200 messages = 40,000 posts
- SQL injection protected (parameterized queries)
- HTML/XSS injection protected (escaped + sanitized)

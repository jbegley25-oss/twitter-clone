"""TweetBox — a Twitter clone built with FastAPI and SQLite3."""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

DB = 'twitter.db'
SECRET = 'tweetbox-super-secret-key-change-in-prod'
PAGE_SIZE = 50
MAX_BODY_LEN = 2000
MAX_BIO_LEN = 500
MAX_PASSWORD_LEN = 200

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET)
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')


# ---------------------------------------------------------------------------
# Global error handlers — never show raw JSON or stack traces to users
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Turn FastAPI 422 validation errors into friendly HTML redirects."""
    return RedirectResponse('/', status_code=302)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    user = current_user(request)
    return templates.TemplateResponse('404.html', {'request': request, 'user': user}, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    user = current_user(request)
    return templates.TemplateResponse('500.html', {'request': request, 'user': user}, status_code=500)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        yield con
    finally:
        con.close()


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _safe_page(page) -> int:
    """Clamp page to a valid positive integer."""
    try:
        p = int(page)
    except (TypeError, ValueError):
        return 1
    return max(1, p)


# ---------------------------------------------------------------------------
# Template helpers / filters
# ---------------------------------------------------------------------------

def _urlify(text: str) -> str:
    return re.sub(
        r'(https?://[^\s<>"]+)',
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        text,
    )


def _mentionify(text: str) -> str:
    return re.sub(
        r'@([A-Za-z0-9_]+)',
        r'<a href="/profile/\1">@\1</a>',
        text,
    )


_DANGEROUS_TAGS = re.compile(
    r'<(script|style|iframe|object|embed|form|input|button|link|meta|base)'
    r'[\s>].*?</\1>|<(script|style|iframe|object|embed|form|input|button|link|meta|base)[^>]*/?>',
    re.IGNORECASE | re.DOTALL,
)
_EVENT_ATTRS = re.compile(r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]*)', re.IGNORECASE)
_JAVASCRIPT_HREF = re.compile(r'href\s*=\s*["\']?\s*javascript:', re.IGNORECASE)
_DATA_URI = re.compile(r'src\s*=\s*["\']data:', re.IGNORECASE)


def _sanitize(html: str) -> str:
    html = _DANGEROUS_TAGS.sub('', html)
    html = _EVENT_ATTRS.sub('', html)
    html = _JAVASCRIPT_HREF.sub('href="#"', html)
    html = _DATA_URI.sub('src=""', html)
    return html


def render_body(text: str) -> str:
    """Render message body: escape raw HTML → markdown → urlify → mentionify → sanitize."""
    from markupsafe import escape
    safe_text = str(escape(text))
    html = md.markdown(safe_text, extensions=['nl2br'])
    html = _urlify(html)
    html = _mentionify(html)
    html = _sanitize(html)
    return html


templates.env.filters['render_body'] = render_body


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user(request: Request) -> Optional[dict]:
    try:
        username = request.session.get('username')
    except Exception:
        return None
    if not username:
        return None
    try:
        with get_db() as con:
            row = con.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes — Home
# ---------------------------------------------------------------------------

@app.get('/', response_class=HTMLResponse)
def home(request: Request, page: int = 1):
    page = _safe_page(page)
    user = current_user(request)
    offset = (page - 1) * PAGE_SIZE
    with get_db() as con:
        total = con.execute(
            'SELECT COUNT(*) FROM messages WHERE parent_id IS NULL'
        ).fetchone()[0]
        rows = con.execute('''
            SELECT m.id, m.body, m.created_at, m.edited_at, m.user_id,
                   u.username, u.avatar_seed,
                   (SELECT COUNT(*) FROM messages r WHERE r.parent_id = m.id) AS reply_count
            FROM messages m
            JOIN users u ON m.user_id = u.id
            WHERE m.parent_id IS NULL
            ORDER BY m.created_at DESC
            LIMIT ? OFFSET ?
        ''', (PAGE_SIZE, offset)).fetchall()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    return templates.TemplateResponse('home.html', {
        'request': request, 'user': user, 'messages': rows,
        'page': page, 'total_pages': total_pages,
    })


@app.get('/messages.json')
def messages_json(page: int = 1):
    page = _safe_page(page)
    offset = (page - 1) * PAGE_SIZE
    with get_db() as con:
        rows = con.execute('''
            SELECT m.id, m.body, m.created_at, m.edited_at, u.username
            FROM messages m
            JOIN users u ON m.user_id = u.id
            WHERE m.parent_id IS NULL
            ORDER BY m.created_at DESC
            LIMIT ? OFFSET ?
        ''', (PAGE_SIZE, offset)).fetchall()
    return JSONResponse([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.get('/login', response_class=HTMLResponse)
def login_get(request: Request):
    if current_user(request):
        return RedirectResponse('/', status_code=302)
    return templates.TemplateResponse('login.html', {'request': request, 'user': None, 'error': None})


@app.post('/login', response_class=HTMLResponse)
def login_post(request: Request, username: str = Form(default=''), password: str = Form(default='')):
    username = username.strip()[:MAX_PASSWORD_LEN]
    if not username or not password:
        return templates.TemplateResponse('login.html', {
            'request': request, 'user': None,
            'error': 'Invalid username or password.',
        }, status_code=401)
    with get_db() as con:
        row = con.execute(
            'SELECT * FROM users WHERE username=? AND password_hash=?',
            (username, hash_password(password[:MAX_PASSWORD_LEN])),
        ).fetchone()
    if not row:
        return templates.TemplateResponse('login.html', {
            'request': request, 'user': None,
            'error': 'Invalid username or password.',
        }, status_code=401)
    request.session['username'] = row['username']
    return RedirectResponse('/', status_code=302)


@app.get('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/', status_code=302)


# ---------------------------------------------------------------------------
# Routes — Create user
# ---------------------------------------------------------------------------

@app.get('/create_user', response_class=HTMLResponse)
def create_user_get(request: Request):
    if current_user(request):
        return RedirectResponse('/', status_code=302)
    return templates.TemplateResponse('create_user.html', {'request': request, 'user': None, 'error': None})


@app.post('/create_user', response_class=HTMLResponse)
def create_user_post(
    request: Request,
    username: str = Form(default=''),
    password: str = Form(default=''),
    password2: str = Form(default=''),
):
    username = username.strip()
    error = None
    if not username:
        error = 'Username cannot be empty.'
    elif len(username) > 50:
        error = 'Username too long (max 50 characters).'
    elif not re.match(r'^[A-Za-z0-9_]+$', username):
        error = 'Username may only contain letters, numbers, and underscores.'
    elif not password:
        error = 'Password cannot be empty.'
    elif len(password) > MAX_PASSWORD_LEN:
        error = f'Password too long (max {MAX_PASSWORD_LEN} characters).'
    elif password != password2:
        error = 'Passwords do not match.'

    if error:
        return templates.TemplateResponse('create_user.html', {
            'request': request, 'user': None, 'error': error,
        }, status_code=400)

    try:
        with get_db() as con:
            con.execute(
                'INSERT INTO users (username, password_hash, avatar_seed) VALUES (?,?,?)',
                (username, hash_password(password), username),
            )
            con.commit()
    except sqlite3.IntegrityError:
        return templates.TemplateResponse('create_user.html', {
            'request': request, 'user': None,
            'error': f'Username "{username}" is already taken.',
        }, status_code=409)

    request.session['username'] = username
    return RedirectResponse('/', status_code=302)


# ---------------------------------------------------------------------------
# Routes — Messages
# ---------------------------------------------------------------------------

@app.get('/create_message', response_class=HTMLResponse)
def create_message_get(request: Request, reply_to: Optional[int] = None):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    parent = None
    if reply_to:
        with get_db() as con:
            row = con.execute(
                'SELECT m.*, u.username FROM messages m JOIN users u ON m.user_id=u.id WHERE m.id=?',
                (reply_to,)
            ).fetchone()
            parent = dict(row) if row else None
    return templates.TemplateResponse('create_message.html', {
        'request': request, 'user': user, 'parent': parent, 'error': None,
    })


@app.post('/create_message', response_class=HTMLResponse)
def create_message_post(
    request: Request,
    body: str = Form(default=''),
    parent_id: Optional[int] = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)

    body = body.strip()
    error = None
    if not body:
        error = 'Message cannot be empty.'
    elif len(body) > MAX_BODY_LEN:
        error = f'Message too long (max {MAX_BODY_LEN} characters).'

    # Validate parent exists if provided
    parent = None
    if parent_id is not None:
        with get_db() as con:
            row = con.execute(
                'SELECT m.*, u.username FROM messages m JOIN users u ON m.user_id=u.id WHERE m.id=?',
                (parent_id,)
            ).fetchone()
            parent = dict(row) if row else None
        if parent is None:
            parent_id = None  # orphan reply protection

    if error:
        return templates.TemplateResponse('create_message.html', {
            'request': request, 'user': user,
            'error': error, 'parent': parent,
        }, status_code=400)

    with get_db() as con:
        con.execute(
            'INSERT INTO messages (user_id, body, created_at, parent_id) VALUES (?,?,?,?)',
            (user['id'], body, datetime.now().isoformat(), parent_id),
        )
        con.commit()

    if parent_id:
        return RedirectResponse(f'/message/{parent_id}', status_code=302)
    return RedirectResponse('/', status_code=302)


@app.get('/edit_message/{message_id}', response_class=HTMLResponse)
def edit_message_get(request: Request, message_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    with get_db() as con:
        row = con.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row or row['user_id'] != user['id']:
        return RedirectResponse('/', status_code=302)
    return templates.TemplateResponse('edit_message.html', {
        'request': request, 'user': user, 'message': dict(row), 'error': None,
    })


@app.post('/edit_message/{message_id}', response_class=HTMLResponse)
def edit_message_post(request: Request, message_id: int, body: str = Form(default='')):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    body = body.strip()

    with get_db() as con:
        row = con.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
        if not row or row['user_id'] != user['id']:
            return RedirectResponse('/', status_code=302)
        if not body:
            return templates.TemplateResponse('edit_message.html', {
                'request': request, 'user': user, 'message': dict(row),
                'error': 'Message cannot be empty.',
            }, status_code=400)
        if len(body) > MAX_BODY_LEN:
            return templates.TemplateResponse('edit_message.html', {
                'request': request, 'user': user, 'message': dict(row),
                'error': f'Message too long (max {MAX_BODY_LEN} characters).',
            }, status_code=400)
        con.execute(
            'UPDATE messages SET body=?, edited_at=? WHERE id=?',
            (body, datetime.now().isoformat(), message_id),
        )
        con.commit()

    parent_id = row['parent_id']
    if parent_id:
        return RedirectResponse(f'/message/{parent_id}', status_code=302)
    return RedirectResponse('/', status_code=302)


@app.get('/delete_message/{message_id}')
def delete_message(request: Request, message_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    with get_db() as con:
        row = con.execute('SELECT user_id, parent_id FROM messages WHERE id=?', (message_id,)).fetchone()
        if row and row['user_id'] == user['id']:
            con.execute('DELETE FROM messages WHERE id=?', (message_id,))
            con.commit()
            if row['parent_id']:
                return RedirectResponse(f'/message/{row["parent_id"]}', status_code=302)
    return RedirectResponse('/', status_code=302)


# ---------------------------------------------------------------------------
# Routes — Profile
# ---------------------------------------------------------------------------

@app.get('/profile/{username}', response_class=HTMLResponse)
def profile(request: Request, username: str):
    user = current_user(request)
    with get_db() as con:
        profile_user = con.execute(
            'SELECT * FROM users WHERE username=?', (username,)
        ).fetchone()
        if not profile_user:
            return templates.TemplateResponse('404.html', {
                'request': request, 'user': user,
            }, status_code=404)
        msgs = con.execute('''
            SELECT m.*, u.username, u.avatar_seed
            FROM messages m JOIN users u ON m.user_id=u.id
            WHERE m.user_id=? AND m.parent_id IS NULL
            ORDER BY m.created_at DESC LIMIT 20
        ''', (profile_user['id'],)).fetchall()
        msg_count = con.execute(
            'SELECT COUNT(*) FROM messages WHERE user_id=?', (profile_user['id'],)
        ).fetchone()[0]
    return templates.TemplateResponse('profile.html', {
        'request': request, 'user': user,
        'profile_user': dict(profile_user),
        'messages': msgs, 'msg_count': msg_count,
        'delete_error': None,
    })


@app.post('/profile/{username}/edit', response_class=HTMLResponse)
def profile_edit(request: Request, username: str, bio: str = Form(default='')):
    user = current_user(request)
    if not user or user['username'] != username:
        return RedirectResponse(f'/profile/{username}', status_code=302)
    bio = bio.strip()[:MAX_BIO_LEN]
    with get_db() as con:
        con.execute('UPDATE users SET bio=? WHERE username=?', (bio, username))
        con.commit()
    return RedirectResponse(f'/profile/{username}', status_code=302)


# ---------------------------------------------------------------------------
# Routes — Delete account
# ---------------------------------------------------------------------------

@app.post('/delete_account')
def delete_account(request: Request, password: str = Form(default='')):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    if not password or user['password_hash'] != hash_password(password[:MAX_PASSWORD_LEN]):
        with get_db() as con:
            msgs = con.execute('''
                SELECT m.*, u.username, u.avatar_seed
                FROM messages m JOIN users u ON m.user_id=u.id
                WHERE m.user_id=? AND m.parent_id IS NULL
                ORDER BY m.created_at DESC LIMIT 20
            ''', (user['id'],)).fetchall()
            msg_count = con.execute(
                'SELECT COUNT(*) FROM messages WHERE user_id=?', (user['id'],)
            ).fetchone()[0]
        return templates.TemplateResponse('profile.html', {
            'request': request, 'user': user,
            'profile_user': user, 'messages': msgs, 'msg_count': msg_count,
            'delete_error': 'Incorrect password.',
        }, status_code=400)
    with get_db() as con:
        con.execute('DELETE FROM messages WHERE user_id=?', (user['id'],))
        con.execute('DELETE FROM users WHERE id=?', (user['id'],))
        con.commit()
    request.session.clear()
    return RedirectResponse('/', status_code=302)


# ---------------------------------------------------------------------------
# Routes — Change password
# ---------------------------------------------------------------------------

@app.get('/change_password', response_class=HTMLResponse)
def change_password_get(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    return templates.TemplateResponse('change_password.html', {
        'request': request, 'user': user, 'error': None, 'success': None,
    })


@app.post('/change_password', response_class=HTMLResponse)
def change_password_post(
    request: Request,
    old_password: str = Form(default=''),
    new_password: str = Form(default=''),
    new_password2: str = Form(default=''),
):
    user = current_user(request)
    if not user:
        return RedirectResponse('/login', status_code=302)
    error = None
    if not old_password or user['password_hash'] != hash_password(old_password[:MAX_PASSWORD_LEN]):
        error = 'Old password is incorrect.'
    elif not new_password:
        error = 'New password cannot be empty.'
    elif len(new_password) > MAX_PASSWORD_LEN:
        error = f'Password too long (max {MAX_PASSWORD_LEN} characters).'
    elif new_password != new_password2:
        error = 'New passwords do not match.'
    if error:
        return templates.TemplateResponse('change_password.html', {
            'request': request, 'user': user, 'error': error, 'success': None,
        }, status_code=400)
    with get_db() as con:
        con.execute(
            'UPDATE users SET password_hash=? WHERE id=?',
            (hash_password(new_password), user['id']),
        )
        con.commit()
    return templates.TemplateResponse('change_password.html', {
        'request': request, 'user': user, 'error': None,
        'success': 'Password updated successfully!',
    })


# ---------------------------------------------------------------------------
# Routes — Search
# ---------------------------------------------------------------------------

@app.get('/search', response_class=HTMLResponse)
def search(request: Request, q: str = '', page: int = 1):
    page = _safe_page(page)
    user = current_user(request)
    results = []
    total = 0
    q = q[:200]  # cap query length
    if q:
        offset = (page - 1) * PAGE_SIZE
        with get_db() as con:
            try:
                total = con.execute(
                    'SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?', (q,),
                ).fetchone()[0]
                results = con.execute('''
                    SELECT m.id, m.body, m.created_at, m.edited_at, m.user_id,
                           u.username, u.avatar_seed
                    FROM messages_fts f
                    JOIN messages m ON f.rowid = m.id
                    JOIN users u ON m.user_id = u.id
                    WHERE messages_fts MATCH ?
                    ORDER BY m.created_at DESC
                    LIMIT ? OFFSET ?
                ''', (q, PAGE_SIZE, offset)).fetchall()
            except sqlite3.OperationalError:
                like = f'%{q}%'
                total = con.execute(
                    'SELECT COUNT(*) FROM messages WHERE body LIKE ?', (like,)
                ).fetchone()[0]
                results = con.execute('''
                    SELECT m.id, m.body, m.created_at, m.edited_at, m.user_id,
                           u.username, u.avatar_seed
                    FROM messages m JOIN users u ON m.user_id=u.id
                    WHERE m.body LIKE ?
                    ORDER BY m.created_at DESC
                    LIMIT ? OFFSET ?
                ''', (like, PAGE_SIZE, offset)).fetchall()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return templates.TemplateResponse('search.html', {
        'request': request, 'user': user, 'q': q,
        'results': results, 'total': total,
        'page': page, 'total_pages': total_pages,
    })


# ---------------------------------------------------------------------------
# Routes — Replies / Thread
# ---------------------------------------------------------------------------

@app.get('/message/{message_id}', response_class=HTMLResponse)
def message_thread(request: Request, message_id: int):
    user = current_user(request)
    with get_db() as con:
        parent = con.execute('''
            SELECT m.*, u.username, u.avatar_seed
            FROM messages m JOIN users u ON m.user_id=u.id
            WHERE m.id=?
        ''', (message_id,)).fetchone()
        if not parent:
            return templates.TemplateResponse('404.html', {
                'request': request, 'user': user,
            }, status_code=404)
        replies = con.execute('''
            SELECT m.*, u.username, u.avatar_seed
            FROM messages m JOIN users u ON m.user_id=u.id
            WHERE m.parent_id=?
            ORDER BY m.created_at ASC
        ''', (message_id,)).fetchall()
    return templates.TemplateResponse('thread.html', {
        'request': request, 'user': user,
        'parent': dict(parent), 'replies': replies,
    })


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=8000, reload=True)

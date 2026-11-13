import hashlib
import hmac
import base64
import json
import time
from . import config


def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()


def make_token(user: dict) -> str:
    payload = {
        'uid': user['id'],
        'username': user['username'],
        'role': user.get('role', 'doctor'),
        'exp': time.time() + config.TOKEN_EXPIRE,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).decode('ascii')
    sig = hmac.new(config.SECRET_KEY.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{body}.{sig}'


def verify_token(token: str):
    try:
        body, sig = token.split('.')
        expect = hmac.new(config.SECRET_KEY.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body.encode('ascii')).decode('utf-8'))
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None

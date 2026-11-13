from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..database import get_conn
from ..auth import hash_password, make_token, verify_token

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


def require_user(request: Request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail='登录已失效，请重新登录')
    return payload


@router.post('/login')
def login(body: LoginBody):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM users WHERE username=%s', (body.username,))
            user = cur.fetchone()
        if not user or user['password_hash'] != hash_password(body.password):
            raise HTTPException(status_code=400, detail='用户名或密码错误')
        token = make_token(user)
        return {
            'token': token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'role': user['role'],
                'real_name': user['real_name'],
            },
        }
    finally:
        conn.close()


@router.get('/me')
def me(user=Depends(require_user)):
    return user

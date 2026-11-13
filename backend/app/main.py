from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import auth, cases, analyze

app = FastAPI(title='心脏CT 3D可视化医疗辅助系统', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth.router, prefix='/api/auth', tags=['认证'])
app.include_router(cases.router, prefix='/api', tags=['病例'])
app.include_router(analyze.router, prefix='/api', tags=['AI分析'])


@app.get('/api/health')
def health():
    return {'status': 'ok', 'service': 'heart3d-backend'}

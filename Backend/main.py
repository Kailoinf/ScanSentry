from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel, Session, create_engine, select, func
from sqlalchemy.pool import StaticPool

# ========== 配置 ==========
DB_PATH = Path(__file__).parent / "logs.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# ========== 数据模型：极简四字段 ==========
class AccessLog(SQLModel, table=True):
    __tablename__ = "access_logs"

    id: int | None = Field(default=None, primary_key=True)
    client_ip: str = Field(index=True)
    method: str
    path: str
    timestamp: str

# ========== 获取真实客户端IP（兼容反向代理） ==========
def get_real_client_ip(request: Request) -> str:
    for header in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
        if val := request.headers.get(header):
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ========== 数据库初始化 ==========
def init_db():
    SQLModel.metadata.create_all(engine)

init_db()

# ========== 响应模型 ==========
class LogEntry(BaseModel):
    id: int
    client_ip: str
    method: str
    path: str
    timestamp: str

    model_config = ConfigDict(from_attributes=True)

class LogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[LogEntry]

class IPStatsEntry(BaseModel):
    client_ip: str
    access_count: int

    model_config = ConfigDict(from_attributes=True)

class IPStatsResponse(BaseModel):
    total: int
    items: list[IPStatsEntry]

# ========== FastAPI 应用 ==========
app = FastAPI(title="ScanSentry", description="轻量级HTTP访问日志服务", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 中间件：记录所有请求（排除/show/me） ==========
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # 不记录 /show/me 路径（查看页面自身请求）
    if request.url.path.startswith("/show/me"):
        return await call_next(request)

    response = await call_next(request)
    with Session(engine) as session:
        session.add(AccessLog(
            client_ip=get_real_client_ip(request),
            method=request.method,
            path=request.url.path + (f"?{request.url.query}" if request.url.query else ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))
        session.commit()
    return response

# ========== API端点 ==========
@app.get("/show/me/logs", response_model=LogListResponse)
def get_logs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)):
    with Session(engine) as session:
        total = session.exec(select(func.count(AccessLog.id))).one()
        logs = session.exec(
            select(AccessLog).order_by(AccessLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
    return LogListResponse(total=total, page=page, page_size=page_size, items=list(logs))

@app.get("/show/me/ip", response_model=IPStatsResponse)
def get_ip_stats(limit: int = Query(50, ge=1, le=500)):
    with Session(engine) as session:
        results = session.exec(
            select(AccessLog.client_ip, func.count(AccessLog.id).label("access_count"))
            .group_by(AccessLog.client_ip)
            .order_by(func.count(AccessLog.id).desc())
            .limit(limit)
        ).all()
    items = [IPStatsEntry(client_ip=r.client_ip, access_count=r.access_count) for r in results]
    return IPStatsResponse(total=len(items), items=items)

@app.get("/show/me/overview")
def get_overview():
    with Session(engine) as session:
        total = session.exec(select(func.count(AccessLog.id))).one()
        unique_ips = session.exec(select(func.count(func.distinct(AccessLog.client_ip)))).one()
    return {"total_requests": total, "unique_ips": unique_ips}

@app.get("/show/me/path")
def get_path_stats(limit: int = Query(50, ge=1, le=500)):
    with Session(engine) as session:
        results = session.exec(
            select(AccessLog.path, func.count(AccessLog.id).label("access_count"))
            .group_by(AccessLog.path)
            .order_by(func.count(AccessLog.id).desc())
            .limit(limit)
        ).all()
    items = [{"path": r.path, "access_count": r.access_count} for r in results]
    return {"total": len(items), "items": items}

@app.get("/show/me/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

# ========== 入口 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
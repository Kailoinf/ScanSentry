from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel, Session, create_engine, select, func
from sqlalchemy.pool import StaticPool
import httpx

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

# ========== 数据模型：IP 信息 ==========
class IPInfo(SQLModel, table=True):
    __tablename__ = "ip_info"
    ip: str = Field(primary_key=True)
    location: str = Field(default="")
    isp: str = Field(default="")

# ========== 获取真实客户端IP（兼容反向代理） ==========
def get_real_client_ip(request: Request) -> str:
    for header in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
        if val := request.headers.get(header):
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ========== 获取 IP 信息 ==========
async def fetch_ip_info(ip: str) -> tuple[str, str]:
    url = f"https://api.ip2location.io/?ip={ip}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=5.0)
            data = resp.json()
            if data.get("country_name"):
                city = data.get("city_name") or ""
                region = data.get("region_name") or ""
                country = data.get("country_name") or ""
                parts = [p for p in [city, region, country] if p]
                location = ", ".join(parts)
                isp = data.get("as") or ""
                return location, isp
    except Exception:
        pass
    return "", ""

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
    location: str = ""
    isp: str = ""

    model_config = ConfigDict(from_attributes=True)

class IPStatsResponse(BaseModel):
    total: int
    page: int
    page_size: int
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
    try:
        client_ip = get_real_client_ip(request)
        # 限制 path 长度，防止恶意数据
        raw_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        path = raw_path[:2048] if len(raw_path) > 2048 else raw_path

        with Session(engine) as session:
            session.add(AccessLog(
                client_ip=client_ip,
                method=request.method,
                path=path,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()
            # 仅在该IP在 ip_info 表中不存在时才查询
            ip_row = session.get(IPInfo, client_ip)
            if ip_row is None:
                try:
                    location, isp = await fetch_ip_info(client_ip)
                    session.add(IPInfo(ip=client_ip, location=location, isp=isp))
                    session.commit()
                except Exception:
                    session.rollback()  # 忽略 IP 信息插入失败（可能是并发插入）
    except Exception:
        pass  # 日志记录失败不应影响正常请求
    return response

# ========== API端点 ==========
@app.get("/show/me/logs", response_model=LogListResponse)
def get_logs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    with Session(engine) as session:
        total = session.exec(select(func.count(AccessLog.id))).one()
        logs = session.exec(
            select(AccessLog).order_by(AccessLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
    return LogListResponse(total=total, page=page, page_size=page_size, items=list(logs))

@app.get("/show/me/ip", response_model=IPStatsResponse)
def get_ip_stats(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    with Session(engine) as session:
        # 先获取总数
        total = session.exec(select(func.count(func.distinct(AccessLog.client_ip)))).one()
        # 获取分页后的 IP 列表
        results = session.exec(
            select(AccessLog.client_ip, func.count(AccessLog.id).label("access_count"))
            .group_by(AccessLog.client_ip)
            .order_by(func.count(AccessLog.id).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items: list[IPStatsEntry] = []
        for r in results:
            ip = r.client_ip
            count = r.access_count
            ip_info = session.get(IPInfo, ip)
            location = ip_info.location if ip_info else ""
            isp = ip_info.isp if ip_info else ""
            items.append(IPStatsEntry(client_ip=ip, access_count=count, location=location, isp=isp))
    return IPStatsResponse(total=total, page=page, page_size=page_size, items=items)

@app.get("/show/me/overview")
def get_overview():
    with Session(engine) as session:
        total = session.exec(select(func.count(AccessLog.id))).one()
        unique_ips = session.exec(select(func.count(func.distinct(AccessLog.client_ip)))).one()
    return {"total_requests": total, "unique_ips": unique_ips}

@app.get("/show/me/path")
def get_path_stats(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    with Session(engine) as session:
        # 先获取总数
        total = session.exec(select(func.count(func.distinct(AccessLog.path)))).one()
        # 获取分页后的 Path 列表
        results = session.exec(
            select(AccessLog.path, func.count(AccessLog.id).label("access_count"))
            .group_by(AccessLog.path)
            .order_by(func.count(AccessLog.id).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    items = [{"path": r.path, "access_count": r.access_count} for r in results]
    return {"total": total, "page": page, "page_size": page_size, "items": items}

@app.get("/show/me/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

# ========== 入口 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)

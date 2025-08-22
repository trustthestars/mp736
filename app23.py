from fastapi import FastAPI, Request, Response, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from jose.backends import base
from pydantic import root_validator
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import secrets

from starlette.status import HTTP_103_EARLY_HINTS

# Configuration
SECRET_KEY = secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Mock user database
fake_users_db = {
    "user1": {
        "username": "user1",
        "password": "password123",  # In production, use hashed passwords
        "symbols": ["AAPL", "MSFT", "GOOGL"]
    }
}

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.update({
            # Disallow 3rd-party trackers and lock content to your domain/CDN
            "Content-Security-Policy":
                "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self' wss://your-domain.example; frame-ancestors 'none'; base-uri 'none'",
            # Strip referrers to avoid leaking URLs/IDs to other sites
            "Referrer-Policy": "no-referrer",
            # Respect Do Not Track / Global Privacy Control
            "Permissions-Policy": "interest-cohort=(), browsing-topics=(), geolocation=(), microphone=(), camera=()",
            # Clickjacking & MIME sniffing
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            # Cross-origin isolation (better for security & some perf APIs)
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            # HSTS (enable only when 100% HTTPS)
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload"
        })
        return resp

app = FastAPI()
app.add_middleware(SecurityHeaders)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.your-domain.example"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True
)

from fastapi import WebSocket, WebSocketDisconnect, status
from jose import jwt, JWTError
ALLOWED_ORIGINS = {"https://app.your-domain.example"}

# Login endpoint
@app.post("/login")
async def login(username: str, password: str):
    user = fake_users_db.get(username)
    if not user or user["password"] != password:  # In production, use proper password hashing
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "symbols": user["symbols"]},
        expires_delta=access_token_expires
    )
    
    response = JSONResponse(content={"access_token": access_token, "token_type": "bearer"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        max_age=1800,  # 30 minutes
        expires=1800,   # 30 minutes
        secure=True,    # In production, set to True for HTTPS only
        samesite="lax"  # or "strict" based on your requirements
    )
    return response

# WebSocket endpoint with token validation
@app.websocket("/ws/{symbol}")
async def ws_stock(ws: WebSocket, symbol: str, token: str = None):
    # Check origin
    origin = ws.headers.get("origin")
    if origin not in ALLOWED_ORIGINS:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    # Get token from query params or cookies
    if not token:
        cookie_header = ws.headers.get("cookie")
        if cookie_header:
            cookies = {c.split("=")[0]: c.split("=")[1] for c in cookie_header.split("; ")}
            token = cookies.get("session")
    
    if not token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    try:
        # Verify token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_aud": False})
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=400, detail="Invalid token")
        
        # Check if symbol is allowed for this user
        if symbol.upper() not in payload.get("symbols", []):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        # Accept the WebSocket connection
        await ws.accept()
        
        # Here you would add your WebSocket message handling logic
        while True:
            data = await ws.receive_text()
            await ws.send_text(f"Message received for {symbol}: {data}")
            
    except JWTError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except WebSocketDisconnect:
        print("Client disconnected")
        return

from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import json, os, yaml, time, asyncio, hashlib, random, uuid
from typing import Dict, Any, AsyncGenerator, List, Optional
from contextlib import asynccontextmanager
import uvicorn

# Configuration
ROUTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes.yaml')
FHIR_ENDPOINT = os.getenv("FHIR_ENDPOINT", "https://api.acme-hospital.example/fhir")

# Initialize FastAPI with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.is_running = True
    app.state.message_queue = asyncio.Queue()
    app.state.stats = {
        'total_messages': 0,
        'successful_messages': 0,
        'error_messages': 0
    }
    
    # Start the message processor task
    app.state.processor_task = asyncio.create_task(process_messages(app))
    
    yield
    
    # Shutdown
    app.state.is_running = False
    if 'processor_task' in app.state:
        app.state.processor_task.cancel()
        try:
            await app.state.processor_task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Sector Proxy", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Load routes config
try:
    routes = yaml.safe_load(open(ROUTES_FILE))
except FileNotFoundError:
    routes = {"default_ttl": 3600}  # Default config if routes file doesn't exist

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"Error broadcasting message: {e}")

manager = ConnectionManager()

class Message(BaseModel):
    envelope: dict
    headers: dict = {}
    payload: dict

def route_subject(env: dict) -> str:
    return f"sector.{env['to_sector']}.{env.get('to_partner', 'any')}"

def apply_checks(msg: Message):
    payload_bytes = json.dumps(msg.payload, separator=(",", ":")).encode()
    if "hash" in msg.headers:
        if not hashlib.sha256(payload_bytes).hexdigest() == msg.headers["hash"].split(':')[-1]:
            raise HTTPException(400, "payload hash mismatch")

        ttl = msg.envelope.get("ttl_seconds", routes.get("default_ttl", 3600))
        msg.envelope["expires_at"] = int(time.time()) + int(ttl)

class MockMessage:
    def __init__(self, data: Dict[str, Any], subject: str):
        self.data = json.dumps(data).encode()
        self.subject = subject
        self.id = str(uuid.uuid4())
        self.timestamp = time.time()
    
    async def ack(self):
        msg = {
            "type": "ack",
            "subject": self.subject,
            "text": "Message acknowledged",
            "timestamp": self.timestamp
        }
        await manager.broadcast(msg)
        print("✓ Message acknowledged")
    
    async def nak(self, delay: int = 0):
        msg = {
            "type": "error",
            "subject": self.subject,
            "text": f"Negative acknowledgment received, will retry after {delay} seconds",
            "timestamp": self.timestamp
        }
        await manager.broadcast(msg)
        print(f"✗ Negative acknowledgment received, will retry after {delay} seconds")
    
    async def term(self):
        msg = {
            "type": "error",
            "subject": self.subject,
            "text": "Message terminated - unrecoverable error",
            "timestamp": self.timestamp
        }
        await manager.broadcast(msg)
        print("✗ Message terminated")

async def mock_subscribe(app: FastAPI) -> AsyncGenerator[MockMessage, None]:
    """Generate mock messages for testing"""
    sample_messages = [
        {"payload": {"resourceType": "Bundle", "type": "message"}, "subject": "sector.health.1"},
        {"payload": {"resourceType": "Bundle", "type": "diagnostic"}, "subject": "sector.health.2"},
        {"payload": {"resourceType": "Bundle", "type": "observation"}, "subject": "sector.health.3"},
    ]
    
    while app.state.is_running:  # Keep generating messages while running
        for msg in sample_messages:
            if not app.state.is_running:
                break
                
            # Check if there are any manual messages in the queue
            try:
                if not app.state.message_queue.empty():
                    manual_msg = await app.state.message_queue.get()
                    yield MockMessage(manual_msg, f"sector.health.{manual_msg.get('type', 'custom')}")
                    app.state.message_queue.task_done()
                    continue
            except Exception as e:
                print(f"Error processing manual message: {e}")
            
            # Otherwise, generate a sample message
            yield MockMessage(msg, msg["subject"])
            await asyncio.sleep(2)  # Simulate delay between messages

async def process_messages(app: FastAPI):
    """Process messages from the mock queue"""
    print("Starting mock message processor. Press Ctrl+C to exit.")
    
    try:
        async for msg in mock_subscribe(app):
            try:
                # Update stats
                app.state.stats['total_messages'] += 1
                
                # Notify UI of new message
                await manager.broadcast({
                    "type": "info",
                    "subject": msg.subject,
                    "text": f"Received message on {msg.subject}",
                    "data": json.loads(msg.data.decode())
                })
                
                print(f"\n📨 Received message on {msg.subject}")
                body = json.loads(msg.data)
                
                # Simulate processing
                print(f"🔄 Processing: {json.dumps(body, indent=2)}")
                
                # Simulate FHIR server request with timeout
                success = random.random() > 0.3  # 70% success rate
                
                # Simulate network delay (0.1-1 second)
                await asyncio.sleep(random.uniform(0.1, 1.0))
                
                if success:
                    app.state.stats['successful_messages'] += 1
                    print("✅ Successfully processed message")
                    await msg.ack()
                else:
                    app.state.stats['error_messages'] += 1
                    print("❌ Simulated processing error")
                    await msg.nak(delay=5)
                    
            except json.JSONDecodeError as e:
                app.state.stats['error_messages'] += 1
                print(f"❌ Failed to decode message: {e}")
                await msg.term()
            except Exception as e:
                app.state.stats['error_messages'] += 1
                print(f"❌ Error processing message: {e}")
                await msg.term()
                
    except asyncio.CancelledError:
        print("\n🛑 Shutdown signal received...")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise

# Web Routes
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/stats")
async def get_stats():
    total = app.state.stats['total_messages']
    success = app.state.stats['successful_messages']
    error = app.state.stats['error_messages']
    
    return {
        "total_messages": total,
        "successful_messages": success,
        "error_messages": error,
        "success_rate": round((success / total) * 100, 2) if total > 0 else 0
    }

@app.post("/send-message")
async def send_message(message_type: str = "message"):
    try:
        message = {
            "payload": {
                "resourceType": "Bundle",
                "type": message_type,
                "timestamp": time.time()
            },
            "type": message_type
        }
        
        await app.state.message_queue.put(message)
        return {"status": "queued", "message_type": message_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
async def stop_processor():
    app.state.is_running = False
    return {"status": "stopping"}

@app.post("/start")
async def start_processor():
    if not app.state.is_running:
        app.state.is_running = True
        app.state.processor_task = asyncio.create_task(process_messages(app))
    return {"status": "started"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Server-Sent Events endpoint
@app.get("/events")
async def events():
    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    return response

async def event_generator():
    while True:
        # This will be replaced with actual event generation
        yield f"data: {json.dumps({'time': time.time()})}\n\n"
        await asyncio.sleep(1)

if __name__ == "__main__":
    uvicorn.run(
        "app23:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=1
    )
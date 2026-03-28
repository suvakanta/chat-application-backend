from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from datetime import datetime
from passlib.context import CryptContext

import models
import schemas
from database import SessionLocal, engine
from models import Message, Room, RoomMember, Base

# ================= INIT ================= #

Base.metadata.create_all(bind=engine)

app = FastAPI()

SECRET_KEY = "secret"
ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


# ================= DB ================= #

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= AUTH ================= #

def create_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    user_id = payload.get("user_id")

    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user_id


# ================= LOGIN ================= #

@app.post("/login")
def login(request: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == request.username).first()

    if not user or not pwd_context.verify(request.password, user.password):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    token = create_token({"user_id": user.id})

    return {"access_token": token, "token_type": "bearer"}


# ================= PROFILE ================= #

@app.get("/profile")
def profile(current_user: int = Depends(get_current_user)):
    return {"message": f"Welcome user {current_user}"}


# ================= PRIVATE MESSAGES ================= #

@app.get("/messages/{user_id}")
def get_messages(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    messages = db.query(Message).filter(
        ((Message.sender_id == current_user) & (Message.receiver_id == user_id)) |
        ((Message.sender_id == user_id) & (Message.receiver_id == current_user))
    ).order_by(Message.id).all()

    return messages


# ================= ROOMS ================= #

@app.post("/rooms", response_model=schemas.RoomResponse)
def create_room(
    room: schemas.RoomCreate,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    new_room = Room(
        name=room.name,
        description=room.description,
        owner_id=current_user
    )

    db.add(new_room)
    db.commit()
    db.refresh(new_room)

    member = RoomMember(room_id=new_room.id, user_id=current_user)
    db.add(member)
    db.commit()

    return new_room


@app.post("/rooms/{room_id}/join")
def join_room(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    existing = db.query(RoomMember).filter(
        RoomMember.room_id == room_id,
        RoomMember.user_id == current_user
    ).first()

    if existing:
        return {"message": "Already joined"}

    member = RoomMember(room_id=room_id, user_id=current_user)
    db.add(member)
    db.commit()

    return {"message": "Joined room"}


# ================= ROOM MESSAGES ================= #

@app.get("/rooms/{room_id}/messages")
def get_room_messages(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    messages = db.query(Message).filter(
        Message.room_id == room_id
    ).order_by(Message.id).all()

    return messages


# ================= WEBSOCKET ================= #

active_connections = {}


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int, token: str):
    await websocket.accept()

    payload = decode_token(token)
    user_id = payload.get("user_id")

    if room_id not in active_connections:
        active_connections[room_id] = []

    active_connections[room_id].append(websocket)

    print(f"User {user_id} connected to room {room_id}")

    try:
        while True:
            data = await websocket.receive_text()

            # ✅ Save group message
            db = SessionLocal()
            new_message = Message(
                sender_id=user_id,
                room_id=room_id,
                content=data,
                created_at=datetime.utcnow()
            )
            db.add(new_message)
            db.commit()
            db.close()

            # ✅ Broadcast message
            for connection in active_connections[room_id]:
                await connection.send_text(f"User {user_id}: {data}")

    except WebSocketDisconnect:
        active_connections[room_id].remove(websocket)

        if not active_connections[room_id]:
            del active_connections[room_id]

        print(f"User {user_id} disconnected from room {room_id}")
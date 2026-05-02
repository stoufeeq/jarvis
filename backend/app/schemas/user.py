from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str | None
    is_active: bool
    is_verified: bool
    telegram_chat_id: str | None = None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: str | None = None
    current_password: str | None = None   # required when changing password
    password: str | None = None           # new password
    telegram_chat_id: str | None = None   # empty string to clear

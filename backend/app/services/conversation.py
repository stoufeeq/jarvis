from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import ChatMessage, Conversation


class ConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_user(self, user_id: int) -> list[Conversation]:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, conversation_id: int, user_id: int) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()

    async def create(self, user_id: int, first_message: str, portfolio_id: int | None) -> Conversation:
        title = first_message.strip()[:80] + ("…" if len(first_message.strip()) > 80 else "")
        conv = Conversation(user_id=user_id, title=title, portfolio_id=portfolio_id)
        self.db.add(conv)
        await self.db.flush()
        await self.db.refresh(conv)
        return conv

    async def add_message(self, conversation_id: int, role: str, content: str) -> ChatMessage:
        msg = ChatMessage(conversation_id=conversation_id, role=role, content=content)
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def delete(self, conversation: Conversation) -> None:
        await self.db.delete(conversation)
        await self.db.flush()

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.database import get_db
from app.models.user import User
from app.services.ai_advisor import AIAdvisor
from app.services.conversation import ConversationService
from app.services.portfolio import PortfolioService

router = APIRouter(prefix="/advisor", tags=["advisor"])


class AdvisorQuery(BaseModel):
    message: str
    portfolio_id: int | None = None
    conversation_id: int | None = None   # continue existing; omit to start new


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: int
    title: str
    portfolio_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


@router.post("/chat")
async def chat(
    payload: AdvisorQuery,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio_context = None
    if payload.portfolio_id:
        svc = PortfolioService(db)
        p = await svc.get(payload.portfolio_id)
        if not p or p.user_id != user.id:
            raise NotFoundError("Portfolio not found")
        portfolio_context = await svc.get_context_for_ai(p)

    conv_svc = ConversationService(db)

    # Get or create the conversation. For existing conversations we capture
    # the message history BEFORE adding the new user message so the model
    # gets the full prior context (and doesn't see the current turn twice).
    history: list[dict] = []
    if payload.conversation_id:
        conv = await conv_svc.get(payload.conversation_id, user.id)
        if not conv:
            raise NotFoundError("Conversation not found")
        history = [{"role": m.role, "content": m.content} for m in conv.messages]
    else:
        conv = await conv_svc.create(
            user_id=user.id,
            first_message=payload.message,
            portfolio_id=payload.portfolio_id,
        )

    advisor = AIAdvisor()
    response = await advisor.chat(
        user_message=payload.message,
        portfolio_context=portfolio_context,
        history=history,
    )

    # Persist both turns only after the model succeeds, so a Gemini failure
    # doesn't leave an orphaned user message with no reply.
    await conv_svc.add_message(conv.id, "user", payload.message)
    await conv_svc.add_message(conv.id, "assistant", response)
    await db.commit()

    return {"response": response, "conversation_id": conv.id}


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ConversationService(db).list_for_user(user.id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await ConversationService(db).get(conversation_id, user.id)
    if not conv:
        raise NotFoundError("Conversation not found")
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ConversationService(db)
    conv = await svc.get(conversation_id, user.id)
    if not conv:
        raise NotFoundError("Conversation not found")
    await svc.delete(conv)
    await db.commit()


@router.get("/portfolio-review/{portfolio_id}")
async def portfolio_review(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p or p.user_id != user.id:
        raise NotFoundError("Portfolio not found")
    context = await svc.get_context_for_ai(p)
    review = await AIAdvisor().portfolio_review(context)
    return {"review": review}


@router.get("/news-digest")
async def news_digest(
    ticker: str | None = None,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    digest = await AIAdvisor().news_digest(db=db, ticker=ticker)
    return {"digest": digest}

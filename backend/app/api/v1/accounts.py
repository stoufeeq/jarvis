from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.user import User
from app.schemas.account import (
    AccountCreate,
    AccountDetail,
    AccountRead,
    AccountTransactionCreate,
    AccountTransactionRead,
    AccountUpdate,
    LiquidityResponse,
)
from app.services.account import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _assert_owner(account, user: User):
    if account.user_id != user.id:
        raise ForbiddenError()


@router.get("/", response_model=list[AccountRead])
async def list_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await AccountService(db).list_for_user(user.id)


@router.post("/", response_model=AccountRead, status_code=201)
async def create_account(
    payload: AccountCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.create(user.id, payload)
    await db.commit()
    return await svc.get(account.id)


@router.get("/liquidity", response_model=LiquidityResponse)
async def get_liquidity(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await AccountService(db).get_liquidity(user.id)


@router.get("/{account_id}", response_model=AccountDetail)
async def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await AccountService(db).get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    return account


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    await svc.update(account, payload)
    await db.commit()
    return await svc.get(account_id)


@router.delete("/{account_id}", status_code=204)
async def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    await svc.delete(account)
    await db.commit()


@router.post("/{account_id}/deposit", response_model=AccountTransactionRead, status_code=201)
async def deposit(
    account_id: int,
    payload: AccountTransactionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    txn = await svc.deposit(account, payload)
    await db.commit()
    return txn


@router.post("/{account_id}/withdraw", response_model=AccountTransactionRead, status_code=201)
async def withdraw(
    account_id: int,
    payload: AccountTransactionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    txn = await svc.withdraw(account, payload)
    await db.commit()
    return txn


@router.get("/{account_id}/transactions", response_model=list[AccountTransactionRead])
async def list_transactions(
    account_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AccountService(db)
    account = await svc.get(account_id)
    if not account:
        raise NotFoundError("Account not found")
    _assert_owner(account, user)
    return account.transactions

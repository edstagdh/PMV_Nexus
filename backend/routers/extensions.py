"""Extensions' actions and panels (2.30.0; backend/extensions.py): what the
interface shows, and the form conversation with an action / panel handler."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import extensions
from ..security import AuthContext, get_current_user

router = APIRouter()


class FormStep(BaseModel):
    item_ids: list[str] = Field(default_factory=list, max_length=5000)
    step: Optional[str] = Field(None, max_length=200)
    values: dict = Field(default_factory=dict)


@router.get("/api/extensions")
def list_extensions(user: AuthContext = Depends(get_current_user)):
    return extensions.summary(user)


async def _run(handler, payload: FormStep, user: AuthContext) -> dict:
    ctx = extensions.ActionContext(values=payload.values, step=payload.step, item_ids=payload.item_ids, user=user)
    try:
        return await extensions.run_handler(handler, ctx)
    except ValueError as exc:  # the extension's message for the person
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/extensions/actions/{action_id}")
async def run_extension_action(action_id: str, payload: FormStep, user: AuthContext = Depends(get_current_user)):
    action = extensions.actions.get(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="No such action")
    if action.permission and not user.has_permission(action.permission):
        raise HTTPException(status_code=403, detail="You don't have permission for this")
    return await _run(action.handler, payload, user)


@router.post("/api/extensions/panels/{panel_id}")
async def run_extension_panel(panel_id: str, payload: FormStep, user: AuthContext = Depends(get_current_user)):
    panel = extensions.panels.get(panel_id)
    if panel is None:
        raise HTTPException(status_code=404, detail="No such panel")
    if not user.has_permission(panel.permission):
        raise HTTPException(status_code=403, detail="You don't have permission for this")
    return await _run(panel.handler, payload, user)

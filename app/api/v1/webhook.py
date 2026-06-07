from fastapi import APIRouter
from app.schemas.email import EmailSchema
from app.core.logger import log_event
from app.orchestrator.orchestrator import run_pipeline

router = APIRouter()


@router.post("/webhook/gmail")
async def gmail_webhook(payload: EmailSchema):

    log_event(
        agent="webhook",
        status="received",
        payload=payload.dict()
    )

    result = await run_pipeline(payload)

    return {
        "status": "processed",
        "flow": result["type"],
        "data": result["data"]
    }

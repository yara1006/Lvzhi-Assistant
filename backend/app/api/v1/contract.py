import uuid
from typing import Annotated
import docx
import pdfplumber
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_yuanqi_client, require_service_auth, resolve_user_id
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models.chat_session import ChatSession
from app.db.models.contract import Contract
from app.db.models.uploaded_file import UploadedFile
from app.db.session import get_db
from app.schemas.common import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ContractGenerateRequest,
    ContractReviewResultResponse,
)
from app.services.conversation import build_user_message, to_yuanqi_messages
from app.services.users import get_or_create_user
from app.services.yuanqi_client import YuanqiClient, extract_assistant_text
from app.services.chat_history import update_session_title_if_default
from app.services.session_utils import ensure_session_owned
router = APIRouter(prefix="/contracts", tags=["contracts"])

MAX_INLINE_TEXT_CHARS = 200_000
REVIEW_FAILED_PREFIX = "REVIEW_FAILED:\n"


def _review_status(contract: Contract) -> str:
    if contract.scene != "review":
        return "unknown"
    if contract.review_result is None:
        return "pending"
    if contract.review_result.startswith(REVIEW_FAILED_PREFIX):
        return "failed"
    return "done"


def _file_type_from_name(name: str | None) -> str:
    if not name:
        return "pdf"
    lower = name.lower()
    if lower.endswith(".docx"):
        return "docx"
    return "pdf"


@router.post(
    "/review/dialog",
    dependencies=[Depends(require_service_auth)],
    response_model=None,
)
async def contract_review_dialog(
    body: ChatCompletionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[YuanqiClient, Depends(get_yuanqi_client)],
    authorization: Annotated[str | None, Header()] = None,
) -> ChatCompletionResponse | StreamingResponse:
    user_id = resolve_user_id(settings, authorization, body.user_id)
    assistant_id = settings.assistant_id_for("contract_review")
    try:
        yuanqi_messages = to_yuanqi_messages(body.messages)
    except ValueError as e:
        raise AppError("invalid_messages", str(e), status_code=400) from e

    base_url = settings.yuanqi_base_url_for("contract_review")
    api_key = settings.yuanqi_api_key_for("contract_review")

    if body.stream:

        async def stream_gen() -> bytes:
            resp = await client.chat_completions_stream(
                assistant_id=assistant_id,
                user_id=user_id,
                messages=yuanqi_messages,
                custom_variables=body.custom_variables,
                base_url=base_url,
                api_key=api_key,
            )
            async for chunk in YuanqiClient.iter_stream_bytes(resp):
                yield chunk

        return StreamingResponse(
            stream_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    data = await client.chat_completions(
        assistant_id=assistant_id,
        user_id=user_id,
        messages=yuanqi_messages,
        stream=False,
        custom_variables=body.custom_variables,
        base_url=base_url,
        api_key=api_key,
    )
    text = extract_assistant_text(data)
    return ChatCompletionResponse(
        id=data.get("id"),
        created=str(data.get("created")) if data.get("created") is not None else None,
        assistant_id=data.get("assistant_id"),
        content=text,
        raw=data if settings.debug else None,
    )


@router.post(
    "/review",
    dependencies=[Depends(require_service_auth)],
)
async def contract_review(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[YuanqiClient, Depends(get_yuanqi_client)],
    authorization: Annotated[str | None, Header()] = None,
    file: UploadFile = File(...),
    notes: str | None = Form(None),
    user_id_body: str | None = Form(None, alias="user_id"),
    session_id: str | None = Form(None),
) -> ContractReviewResultResponse:
    from app.db.models.chat_message import ChatMessage as ChatMessageRow
    
    uid = resolve_user_id(settings, authorization, user_id_body)
    user = await get_or_create_user(session, uid, settings)
    
    # 自动创建会话（如果没有传入 session_id）
    actual_session_id = session_id
    session_row = None # 用于传递给 update_session_title_if_default
    if not actual_session_id:
        actual_session_id = str(uuid.uuid4())
        title = file.filename or "合同审查"
        new_session = ChatSession(
            id=actual_session_id,
            user_id=user.id,
            title=title[:50] if title else "合同审查",
            tool_type="review",
        )
        session.add(new_session)
        await session.flush()
        session_row = new_session
    else:
        session_row = await ensure_session_owned(session, user.id, actual_session_id)

    raw = await _read_limited_upload(file, settings.max_upload_bytes)

    # 根据文件类型提取文本内容
    if file.filename and file.filename.lower().endswith('.docx'):
        try:
            doc = docx.Document(BytesIO(raw))
            contract_text = '\n'.join([p.text for p in doc.paragraphs])
        except Exception:
            contract_text = raw.decode("utf-8", errors="replace")
    elif file.filename and file.filename.lower().endswith('.pdf'):
        try:
            text_parts = []
            with pdfplumber.open(BytesIO(raw)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            contract_text = "\n\n".join(text_parts)
            if not contract_text.strip():
                raise ValueError("PDF contains no extractable text")
        except Exception:
            contract_text = ""
            raise AppError(
                "pdf_no_text",
                "PDF 文件中未找到可提取的文本，可能是扫描件或图片型 PDF，暂不支持 OCR 识别。请将 PDF 转换为文字型 PDF 或直接粘贴合同文本进行审查。",
                status_code=400,
            )
    else:
        contract_text = raw.decode("utf-8", errors="replace")

    if len(contract_text) > MAX_INLINE_TEXT_CHARS:
        contract_text = contract_text[:MAX_INLINE_TEXT_CHARS] + "\n\n[文本已截断]"

    assistant_id = settings.assistant_id_for("contract_review")
    api_key = settings.yuanqi_api_key_for("contract_review")
    cid = str(uuid.uuid4())
    title = file.filename or "合同审查"
    ft = _file_type_from_name(file.filename)

    contract = Contract(
        id=cid,
        user_id=user.id,
        session_id=actual_session_id,
        title=title,
        contract_type=None,
        scene="review",
        review_role="neutral",
    )
    session.add(contract)
    ufile = UploadedFile(
        id=str(uuid.uuid4()),
        user_id=user.id,
        contract_id=cid,
        file_name=file.filename or "upload",
        file_size=len(raw),
        file_type=ft,
        storage_url=None,
    )
    session.add(ufile)
    await session.flush()
    await session.commit()

    # ===== 保存用户消息到聊天记录 =====
    user_message_content = notes or f"上传合同文件：{file.filename}"
    user_msg = ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=actual_session_id,
        role="user",
        content=user_message_content,
        tool_badge="review",
    )
    session.add(user_msg)
    await session.flush()
    # ================================

    # 更新会话标题（如果需要）
    if session_row:
        await update_session_title_if_default(session, session_row, user_message_content)
        await session.flush() # 确保标题更新被持久化

    prompt_parts = [
        "你是一名法律顾问，请对下列合同文本进行风险审查，列出风险点、建议修改方向及依据（如有）。",
    ]
    if notes:
        prompt_parts.append(f"用户补充说明：{notes}")
    prompt_parts.append("--- 合同文本 ---")
    prompt_parts.append(contract_text)
    prompt = "\n".join(prompt_parts)
    messages = build_user_message(prompt)

    try:
        data = await client.chat_completions(
            assistant_id=assistant_id,
            user_id=uid,
            messages=messages,
            stream=False,
            custom_variables=None,
            api_key=api_key, 
        )
        text = extract_assistant_text(data)
        contract.review_result = text
        
        # ===== 保存 AI 回复到聊天记录 =====
        assistant_msg = ChatMessageRow(
            id=str(uuid.uuid4()),
            session_id=actual_session_id,
            role="assistant",
            content=text,
            tool_badge="review",
        )
        session.add(assistant_msg)
        # ================================
        
    except AppError as e:
        contract.review_result = f"{REVIEW_FAILED_PREFIX}{e.message}"
        await session.commit()
        raise
    except Exception as e:
        contract.review_result = f"{REVIEW_FAILED_PREFIX}{str(e)[:2000]}"
        await session.commit()
        raise AppError(
            "review_failed",
            "合同审查调用失败",
            status_code=502,
            detail=str(e)[:500],
        ) from e

    await session.commit()
    st = _review_status(contract)
    err = None
    res = contract.review_result    
    if st == "failed" and res:
        err = res.removeprefix(REVIEW_FAILED_PREFIX).strip()
        res = None
    return ContractReviewResultResponse(
        contract_id=cid,
        status=st,
        result=res,
        error=err,
    )



@router.get(
    "/review/{contract_id}",
    dependencies=[Depends(require_service_auth)],
)
async def get_contract_review(
    contract_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> ContractReviewResultResponse:
    uid = resolve_user_id(settings, authorization, None)
    user = await get_or_create_user(session, uid, settings)
    result = await session.execute(
        select(Contract).where(
            Contract.id == contract_id,
            Contract.user_id == user.id,
            Contract.scene == "review",
        )
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise AppError("not_found", "合同审查记录不存在", status_code=404)
    st = _review_status(contract)
    err = None
    res = contract.review_result
    if st == "failed" and res:
        err = res.removeprefix(REVIEW_FAILED_PREFIX).strip()
        res = None
    elif st == "pending":
        res = None
    return ContractReviewResultResponse(
        contract_id=contract.id,
        status=st,
        result=res,
        error=err,
    )


@router.post(
    "/generate",
    dependencies=[Depends(require_service_auth)],
)
async def contract_generate(
    body: ContractGenerateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[YuanqiClient, Depends(get_yuanqi_client)],
    authorization: Annotated[str | None, Header()] = None,
) -> ChatCompletionResponse:
    user_id = resolve_user_id(settings, authorization, body.user_id)
    user = await get_or_create_user(session, user_id, settings)
    
    # 自动创建会话（如果没有传入 session_id）
    actual_session_id = body.session_id
    session_row = None # 用于传递给 update_session_title_if_default
    if not actual_session_id:
        actual_session_id = str(uuid.uuid4())
        title = body.contract_type or "合同生成"
        new_session = ChatSession(
            id=actual_session_id,
            user_id=user.id,
            title=title[:50] if title else "合同生成",
            tool_type="contract",
        )
        session.add(new_session)
        await session.flush()
        session_row = new_session
    else:
        session_row = await ensure_session_owned(session, user.id, actual_session_id)

    assistant_id = settings.assistant_id_for("contract_generate")
    lines = [
        "请根据以下信息起草合同正文，条款应完整、可执行，并说明必要时的留白项。",
        f"合同类型：{body.contract_type}",
    ]
    if body.parties:
        lines.append(f"当事方：{body.parties}")
    if body.subject_matter:
        lines.append(f"标的/合作内容：{body.subject_matter}")
    if body.extra_requirements:
        lines.append(f"其他要求：{body.extra_requirements}")
    prompt = "\n".join(lines)
    messages = build_user_message(prompt)
    
    # ===== 保存用户消息到聊天记录 =====
    user_message_content = prompt # 使用完整的 prompt 作为用户消息内容
    user_msg = ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=actual_session_id,
        role="user",
        content=user_message_content,
        tool_badge="contract",
    )
    session.add(user_msg)
    await session.flush()
    # ================================

    # 更新会话标题（如果需要）
    if session_row:
        await update_session_title_if_default(session, session_row, user_message_content)
        await session.flush() # 确保标题更新被持久化


    data = await client.chat_completions(
        assistant_id=assistant_id,
        user_id=user_id,
        messages=messages,
        stream=False,
        custom_variables=body.custom_variables,
        api_key=settings.yuanqi_api_key_for("contract_generate"), 
    )
    text = extract_assistant_text(data)
    cid = str(uuid.uuid4())
    contract = Contract(
        id=cid,
        user_id=user.id,
        session_id=actual_session_id,
        title=f"{body.contract_type}",
        contract_type=body.contract_type,
        scene="generate",
        content=text,
    )
    session.add(contract)
    
    # ===== 保存 AI 回复到聊天记录 =====
    assistant_msg = ChatMessageRow(
        id=str(uuid.uuid4()),
        session_id=actual_session_id,
        role="assistant",
        content=text,
        tool_badge="contract",
    )
    session.add(assistant_msg)
    # ================================

    await session.commit()

    return ChatCompletionResponse(
        id=data.get("id"),
        created=str(data.get("created")) if data.get("created") is not None else None,
        assistant_id=data.get("assistant_id"),
        content=text,
        raw=data if settings.debug else None,
        contract_id=cid,
    )


async def _read_limited_upload(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await file.read(64 * 1024)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise AppError(
                "payload_too_large",
                f"上传文件超过限制（{max_bytes} 字节）",
                status_code=413,
            )
        chunks.append(block)
    data = b"".join(chunks)
    if not data:
        raise AppError("empty_file", "上传文件为空", status_code=400)
    return data

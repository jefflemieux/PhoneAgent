import os
import re
import json
import uuid
import base64
import asyncio
import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from twilio.rest import Client
import websockets
import openai
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, From, To
from dotenv import load_dotenv
import redis

# ── Existing config ───────────────────────────────────────────────────────────
load_dotenv()
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
PHONE_NUMBER_FROM = os.getenv('PHONE_NUMBER_FROM')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
EMAIL_FROM = os.getenv('CUSTOM_EMAIL_FROM')
SUMMARY_TEMPLATE_ID = os.getenv('SENDGRID_SUMMARY_TEMPLATE_ID')
raw_domain = os.getenv('CUSTOM_DOMAIN', '')
DOMAIN = re.sub(r'(^\w+:|^)\/\/|\/+$', '', raw_domain)
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

STREAMS_SETTINGS: Dict[str, Dict] = {}
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
logger = logging.getLogger(__name__)
logging.basicConfig(filename='custom_phone_agent.log', level=logging.INFO)


class CallCustomRequest(BaseModel):
    """Request model for a custom call."""
    phone_number: str
    email: str
    system_message: str
    voice: str = "alloy"


app = FastAPI()


@app.post("/call_custom", response_class=JSONResponse)
async def call_custom(req: CallCustomRequest, background_tasks: BackgroundTasks):
    """Endpoint to initiate a custom call."""
    logger.info(f"[call_custom] Received request: {req}")
    session_id = uuid.uuid4().hex
    STREAMS_SETTINGS[session_id] = {
        "system_message": req.system_message,
        "voice": req.voice,
        "email": req.email,
    }
    background_tasks.add_task(make_call_custom, req.phone_number, session_id)
    return {"message": f"Call initiated to {req.phone_number}", "session_id": session_id}

async def check_number_allowed(to: str) -> bool:
    """Check if a number is allowed to be called."""
    try:
        incoming_numbers = client.incoming_phone_numbers.list(phone_number=to)
        if incoming_numbers:
            return True
        outgoing_caller_ids = client.outgoing_caller_ids.list(phone_number=to)
        if outgoing_caller_ids:
            return True
        return False
    except Exception as e:
        logger.info(f"Error checking phone number: {e}")
        return False

async def make_call_custom(phone: str, session_id: str):
    """Create an outbound call using the session_id WebSocket path."""
    if not await check_number_allowed(phone):
        raise ValueError(f"Number {phone} not allowed.")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect>'
        f'<Stream url="wss://{DOMAIN}/media-stream/{session_id}" />'
        f'</Connect></Response>'
    )
    call = client.calls.create(
        from_=PHONE_NUMBER_FROM,
        to=phone,
        twiml=twiml
    )
    logger.info(f"Started call: {call.sid}")

@app.websocket("/media-stream/{session_id}")
async def media_stream(ws: WebSocket, session_id: str):
    """WebSocket endpoint for media streaming between Twilio and OpenAI."""
    logger.info(f"[media-stream] New WS connection for session_id={session_id}")
    settings = STREAMS_SETTINGS.get(session_id)
    if not settings:
        logger.info(f"[media-stream] ❌ no settings found for {session_id}, closing")
        await ws.close()
        return

    await ws.accept()
    logger.info(f"[media-stream] ✓ WS accepted for {session_id}")

    # Connect to OpenAI
    try:
        openai_ws = await websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            },
            origin="https://api.openai.com"
        )
        logger.info("[media-stream] ✓ OpenAI WS handshake succeeded")
    except Exception as e:
        logger.info(f"[media-stream] ❌ OpenAI WS handshake failed: {e}")
        await ws.close()
        return

    transcript: List[str] = []
    stream_sid: Optional[str] = None

    async def twilio_to_openai():
        """Forward audio from Twilio to OpenAI websocket."""
        nonlocal stream_sid
        try:
            async for raw in ws.iter_text():
                data = json.loads(raw)
                if data["event"] == "start":
                    stream_sid = data["start"]["streamSid"]
                    logger.info(f"[media-stream] got start, streamSid={stream_sid}")
                elif data["event"] == "media":
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"]
                    }))
        except Exception:
            logger.info("[media-stream] ✂️ Twilio socket closed")

    async def openai_to_twilio():
        """Forward audio and transcript from OpenAI to Twilio websocket."""
        nonlocal stream_sid
        try:
            async for raw in openai_ws:
                evt = json.loads(raw)
                # Capture USER text
                if evt.get("type") == "conversation.item.input_audio_transcription.completed":
                    logger.info(f"[media-stream] Transcription completed: {evt['transcript']}")
                    transcript.append(f"user: {evt['transcript']}")
                # Capture ASSISTANT text
                if evt.get("type") == "response.audio_transcript.done":
                    logger.info(f"[media-stream] Assistant response: {evt['transcript']}")
                    transcript.append(f"assistant: {evt['transcript']}")
                # Forward audio
                if evt.get("type") == "response.audio.delta" and evt.get("delta"):
                    chunk = base64.b64decode(evt["delta"])
                    payload = base64.b64encode(chunk).decode("utf-8")
                    await ws.send_json({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload}
                    })
        except Exception as e:
            logger.info(f"[media-stream] Error in openai_to_twilio: {e}")

    # Initialize session & first prompt

    await openai_ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "voice": settings["voice"],
            "instructions": settings["system_message"],
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "input_audio_transcription": {"model": "whisper-1"},
        }
    }))
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Please begin by greeting the user."}]
        }
    }))
    await openai_ws.send(json.dumps({"type": "response.create"}))

    # Run both loops until one ends
    t1 = asyncio.create_task(twilio_to_openai())
    t2 = asyncio.create_task(openai_to_twilio())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    logger.info("[media-stream] 🚧 streams terminated, proceeding to summary")

    # Close OpenAI socket (safely)
    try:
        await openai_ws.close()
    except Exception:
        pass

    # Summarize & email BEFORE closing the Twilio WS
    logger.info(f"[media-stream] 🏷 summarizing transcript ({len(transcript)} chunks)...")
    full_text = "\n".join(transcript)
    try:
        summary = await summarize_conversation(full_text)
        logger.info(f"[media-stream] 📰 summary: {summary!r}")
    except Exception as e:
        logger.info(f"[media-stream] ❌ summarization failed: {e}")
        summary = "Sorry, I couldn't generate a summary."

    try:
        await send_summary_email(settings["email"], summary)
        logger.info(f"[media-stream] ✉️ summary email sent to {settings['email']}")
    except Exception as e:
        logger.info(f"[media-stream] ❌ sending summary email failed: {e}")

    # Let FastAPI close the WebSocket
    return





async def summarize_conversation(conv: str) -> str:
    """Summarize a conversation transcript using OpenAI."""
    logger.info(f"[media-stream] 🏷 summarization input length={len(conv)}")
    preview = conv[:200].replace("\n", " ")
    logger.info(f"[media-stream] 🏷 transcript preview: {preview!r}")

    if not conv.strip():
        logger.info("[media-stream] ⚠️ transcript is empty, skipping LLM call")
        return "No conversation content to summarize."

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a concise summarizer."},
                {"role": "user", "content": f"Summarize this call transcript in the language of the call:\n\n{conv}"}
            ]
        )
        logger.info(f"[media-stream] 🏷 summarization response received:\n{resp}")
        summary = resp.choices[0].message.content.strip()
        logger.info(f"[media-stream] 📰 summarization succeeded: {summary!r}")
        return summary
    except Exception as e:
        logger.info(f"[media-stream] ❌ summarization exception: {type(e)}, {e}")
        raise

async def send_summary_email(to_email: str, summary: str):
    """Send a summary email using SendGrid."""
    if not SENDGRID_API_KEY or not EMAIL_FROM or not SUMMARY_TEMPLATE_ID:
        logger.info("[WARN] Missing summary email config.")
        return
    message = Mail(
        from_email=From(EMAIL_FROM),
        to_emails=To(to_email),
    )
    message.template_id = SUMMARY_TEMPLATE_ID
    message.dynamic_template_data = {"summary": summary}
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        res = sg.send(message)
        logger.info(f"Summary sent ({res.status_code}) to {to_email}")
    except Exception as e:
        logger.info(f"Failed to send summary email: {e}")

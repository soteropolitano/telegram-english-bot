import os
import time
import tempfile
import requests
from flask import Flask, request, jsonify
from google import genai
from usage_store import (
    load_usage,
    cost_allowed,
    register_usage,
    estimated_spent_usd as persisted_estimated_spent_usd,
)

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "2500"))
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", str(5 * 1024 * 1024)))
MAX_AUDIO_SECONDS = int(os.getenv("MAX_AUDIO_SECONDS", "90"))
WORKER_HARD_STOP_USD = float(os.getenv("WORKER_HARD_STOP_USD", "8"))

client = genai.Client(api_key=GEMINI_API_KEY)

usage = {
    "month": time.strftime("%Y-%m"),
    "estimated_spent_usd": 0.0
}


def telegram_api(method, payload=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    response = requests.post(url, data=payload, timeout=30)

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:1000]}


    return response


def estimate_cost_usd(text_len: int, audio_bytes: int = 0) -> float:
    approx_input_tokens = max(1, text_len // 4)
    approx_output_tokens = 350

    text_cost = (
        (approx_input_tokens / 1_000_000 * 0.25)
        + (approx_output_tokens / 1_000_000 * 1.50)
    )

    audio_cost = 0.002 if audio_bytes else 0
    return text_cost + audio_cost


def cost_allowed_legacy_unused(estimated: float) -> bool:
    return False


def build_prompt(user_text: str) -> str:
    prompt = (
        'Você é um professor de inglês para brasileiros em um grupo de estudo.\n\n'
        'Tarefa:\n'
        'Corrigir a mensagem em inglês abaixo com foco em gramática, contexto, fluência e naturalidade.\n\n'
        'Regras obrigatórias:\n'
        '- Responda em português.\n'
        '- Seja curto e objetivo.\n'
        '- Não use Markdown.\n'
        '- Não use asteriscos.\n'
        '- Não use negrito.\n'
        '- Não diga que a frase está perfeita se houve correção.\n'
        '- Preserve a intenção original.\n'
        '- Não troque substantivos centrais da frase, como market, store, school, home, company, job, meeting ou nomes próprios.\n'
        '- Não mude lugar, pessoa, objeto, tempo verbal pretendido ou contexto.\n'
        '- Explique somente o erro principal.\n'
        '- Mantenha frases corrigidas e exemplos em inglês.\n'
        '- Se a frase original já estiver correta, diga: "A frase está correta."\n'
        '- Se a versão corrigida já estiver natural, em Mais natural responda apenas: "Não necessário."\n'
        '- Se sugerir uma versão mais natural, ela deve preservar exatamente o mesmo sentido.\n'
        '- Não invente contexto.\n'
        '- Atenção gramatical: "went" é passado simples de "go"; "gone" é o particípio passado. Não chame "went" de particípio.\n\n'
        'Formato exato:\n'
        'Correção:\n'
        '<frase corrigida em inglês>\n\n'
        'Explicação:\n'
        '<explicação curta em português>\n\n'
        'Mais natural:\n'
        '<versão mais natural em inglês, ou "Não necessário.">\n\n'
        'Mensagem:\n'
        f'{user_text}\n'
    )
    return prompt


def correct_text(user_text: str) -> str:
    user_text = user_text.strip()[:MAX_TEXT_CHARS]
    estimated = estimate_text_cost_usd(user_text)

    allowed, info = cost_allowed(estimated)
    if not allowed:
        return (
            "⚠️ Limite mensal interno atingido. "
            f"Gasto estimado atual: US$ {info['estimated_spent_usd']:.4f}. "
            f"Hard stop: US$ {info['worker_hard_stop_usd']:.2f}."
        )

    prompt = build_prompt(user_text)
    model_used = GEMINI_MODEL

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
    except Exception:
        model_used = GEMINI_FALLBACK_MODEL
        response = client.models.generate_content(
            model=GEMINI_FALLBACK_MODEL,
            contents=prompt
        )

    register_usage(
        estimated,
        event_type="text_correction",
        metadata={
            "chars": len(user_text or ""),
            "model": model_used,
        },
    )

    return response.text.strip()[:3500]


def download_telegram_file(file_id: str) -> tuple[str, bytes]:
    file_info = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30
    ).json()

    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    content = requests.get(file_url, timeout=60).content
    return file_path, content


def correct_audio(file_id: str, duration_seconds: int = 0) -> str:
    file_path, content = download_telegram_file(file_id)

    if len(content) > MAX_AUDIO_BYTES:
        return f"⚠️ Áudio grande demais para o MVP. Envie até aproximadamente {MAX_AUDIO_BYTES // 1000000} MB ou escreva a frase em texto."

    estimated = estimate_audio_cost_usd(
        audio_bytes=len(content),
        duration_seconds=duration_seconds,
    )

    allowed, info = cost_allowed(estimated)
    if not allowed:
        return (
            "⚠️ Limite mensal interno atingido. "
            f"Gasto estimado atual: US$ {info['estimated_spent_usd']:.4f}. "
            f"Hard stop: US$ {info['worker_hard_stop_usd']:.2f}."
        )

    suffix = ".ogg" if file_path.endswith((".oga", ".ogg")) else ".audio"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(content)
        tmp_path = f.name

    try:
        uploaded = client.files.upload(file=tmp_path)

        prompt = """
Você é um professor de inglês para brasileiros.

Tarefa:
1. Transcreva o áudio em inglês.
2. Corrija erros de gramática, vocabulário, pronúncia inferida ou naturalidade.
3. Explique os erros em português.
4. Sugira uma versão mais natural em inglês, se fizer sentido.

Regras:
- Seja curto e direto.
- Não use Markdown.
- Não use asteriscos.
- Não use negrito.
- Não invente contexto.
- Se o áudio estiver incompreensível, diga isso claramente.

Formato obrigatório:

Transcrição:
<texto transcrito>

Correção:
<correção em inglês>

Explicação:
<explicação curta em português>

Mais natural:
<versão mais natural em inglês, ou "Não necessário.">
"""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, uploaded]
        )

        register_usage(
            estimated,
            event_type="audio_correction",
            metadata={
                "audio_bytes": len(content),
                "duration_seconds": int(duration_seconds or 0),
                "model": GEMINI_MODEL,
            },
        )

        return response.text.strip()[:3500]

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass



@app.get("/")
def health():
    usage_data = load_usage()
    return jsonify({
        "status": "ok",
        "service": "telegram-english-bot",
        "model": GEMINI_MODEL,
        "fallback_model": GEMINI_FALLBACK_MODEL,
        "estimated_spent_usd": round(float(usage_data.get("estimated_spent_usd", 0.0)), 6),
        "monthly_budget_usd": usage_data.get("monthly_budget_usd"),
        "worker_hard_stop_usd": WORKER_HARD_STOP_USD,
        "max_text_chars": MAX_TEXT_CHARS,
        "max_audio_bytes": MAX_AUDIO_BYTES,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
        "owner_configured": bool(TELEGRAM_OWNER_ID),
        "usage_persistence": "cloud_storage",
    })




@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}

    # 1. Controle de entrada em grupos:
    # Quando o bot é adicionado/removido/promovido em grupo, o Telegram envia my_chat_member.
    # Se quem adicionou não for o owner, o bot avisa e sai.
    my_chat_member = update.get("my_chat_member")
    if my_chat_member:
        chat = my_chat_member.get("chat", {})
        actor = my_chat_member.get("from", {})
        new_status = (my_chat_member.get("new_chat_member", {}) or {}).get("status")

        chat_id = chat.get("id")
        chat_type = chat.get("type", "")
        actor_id = str(actor.get("id", ""))

        joined_statuses = {"member", "administrator"}

        if chat_type in ("group", "supergroup") and new_status in joined_statuses:
            if TELEGRAM_OWNER_ID and actor_id != TELEGRAM_OWNER_ID:
                try:
                    telegram_api("sendMessage", {
                        "chat_id": chat_id,
                        "text": "Este bot só pode ser adicionado a grupos pelo proprietário autorizado."
                    })
                except Exception:
                    pass

                try:
                    telegram_api("leaveChat", {
                        "chat_id": chat_id
                    })
                except Exception:
                    pass

                return jsonify({
                    "status": "left_group",
                    "reason": "added_by_non_owner",
                    "actor_id": actor_id,
                    "chat_id": chat_id
                })

            return jsonify({
                "status": "group_join_authorized",
                "actor_id": actor_id,
                "chat_id": chat_id
            })

        return jsonify({"status": "my_chat_member_ignored"})

    # 2. Mensagens normais
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id:
        return jsonify({"status": "ignored"})

    chat_type = chat.get("type", "private")
    sender = message.get("from", {})
    sender_id = str(sender.get("id", ""))


    # 3. Privado: só responde ao proprietário
    if chat_type == "private" and TELEGRAM_OWNER_ID and sender_id != TELEGRAM_OWNER_ID:
        return jsonify({
            "status": "ignored",
            "reason": "private_owner_only",
            "sender_id": sender_id
        })

    text = message.get("text") or message.get("caption")

    # 4. Texto
    if text:
        original_text = text.strip()

        # Comandos informativos, sem chamada de LLM e sem custo.
        if original_text.startswith("/start"):
            telegram_send_message(chat_id, (
                "Olá. Eu corrijo mensagens em inglês e áudios curtos.\n\n"
                "No privado:\n"
                "- envie uma frase em inglês diretamente\n"
                "- ou envie um áudio curto\n\n"
                "Em grupos:\n"
                "- use /corrigir antes da frase\n\n"
                "Exemplo:\n"
                "/corrigir I have 20 years old and I work with cloud."
            ))
            return jsonify({"ok": True})

        if original_text.startswith("/help"):
            telegram_send_message(chat_id, (
                "Como usar:\n\n"
                "Texto no privado:\n"
                "Envie uma frase em inglês diretamente.\n\n"
                "Texto em grupo:\n"
                "Use /corrigir antes da frase.\n\n"
                "Exemplo:\n"
                "/corrigir I have 20 years old.\n\n"
                "Áudio:\n"
                "Envie um voice message curto.\n"
                "Limite atual: 90 segundos e 3 MB.\n\n"
                "O bot explica os erros em português e sugere uma versão mais natural em inglês."
            ))
            return jsonify({"ok": True})

        # Grupo: só responde com /corrigir
        if chat_type in ("group", "supergroup"):
            if not original_text.startswith("/corrigir"):
                return jsonify({
                    "status": "ignored",
                    "reason": "group_requires_command"
                })

            text_to_correct = original_text.replace("/corrigir", "", 1).strip()

            if not text_to_correct:
                telegram_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "Envie assim: /corrigir I have gone to the market yesterday.",
                    "reply_to_message_id": message.get("message_id")
                })
                return jsonify({"status": "ok", "type": "help"})
        else:
            text_to_correct = original_text

        answer = correct_text(text_to_correct)
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": answer,
            "reply_to_message_id": message.get("message_id")
        })
        return jsonify({"status": "ok", "type": "text"})

    # 5. Áudio
    voice = message.get("voice")
    audio = message.get("audio")

    if voice or audio:
        # Grupo: áudio só com caption /corrigir, para evitar festival de custo.
        if chat_type in ("group", "supergroup"):
            caption = (message.get("caption") or "").strip()
            if not caption.startswith("/corrigir"):
                return jsonify({
                    "status": "ignored",
                    "reason": "group_audio_requires_command"
                })

        media = voice or audio
        duration = int(media.get("duration", 0) or 0)

        if duration and duration > MAX_AUDIO_SECONDS:
            telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": f"⚠️ Áudio longo demais. Envie até {MAX_AUDIO_SECONDS} segundos.",
                "reply_to_message_id": message.get("message_id")
            })
            return jsonify({
                "status": "ignored",
                "reason": "audio_too_long",
                "duration": duration,
                "max_audio_seconds": MAX_AUDIO_SECONDS
            })

        file_id = (voice or audio)["file_id"]
        answer = correct_audio(file_id, duration_seconds=duration)
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": answer,
            "reply_to_message_id": message.get("message_id")
        })
        return jsonify({"status": "ok", "type": "audio"})

    return jsonify({"status": "ignored"})


def estimate_text_cost_usd(text: str) -> float:
    """
    Estimativa simples e conservadora para controle interno.
    Não é billing real da Google. É só o nosso freio de mão, já que humanos precisam
    ser protegidos de si mesmos e de APIs cobradas em dólar.
    """
    chars = len(text or "")
    # Aproximação grosseira: 4 chars/token.
    input_tokens = max(chars / 4, 1)
    # Saída curta esperada do bot.
    output_tokens = 450

    # Estimativa conservadora genérica para flash/lite.
    # Mantemos baixo, mas não zero, porque "quase grátis" ainda não é grátis.
    estimated = ((input_tokens / 1_000_000) * 0.10) + ((output_tokens / 1_000_000) * 0.40)
    return round(max(estimated, 0.000001), 6)


def estimate_audio_cost_usd(audio_bytes: int, duration_seconds: int | float | None = None) -> float:
    """
    Estimativa conservadora para áudio.
    Como o Gemini processa mídia de forma diferente por modelo, usamos aproximação operacional
    para o hard stop interno, não para contabilidade fiscal, obviamente.
    """
    seconds = float(duration_seconds or 0)
    size_mb = float(audio_bytes or 0) / 1_000_000

    # Base mínima + fator por duração/tamanho.
    estimated = 0.00015 + (seconds * 0.00001) + (size_mb * 0.00005)
    return round(max(estimated, 0.000001), 6)



def telegram_send_message(chat_id, text, reply_to_message_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    return telegram_api("sendMessage", payload)


def ensure_cost_allowed_or_reply(chat_id, estimated_increment_usd: float, reason: str) -> bool:
    allowed, info = cost_allowed(estimated_increment_usd)

    if allowed:
        return True

    try:
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": (
                "⚠️ Limite mensal interno atingido. "
                "Não vou processar esta mensagem para evitar custo extra. "
                f"Gasto estimado atual: US$ {info['estimated_spent_usd']:.4f}. "
                f"Hard stop: US$ {info['worker_hard_stop_usd']:.2f}."
            )
        })
    except Exception as exc:
        print(f"[cost] failed to send hard stop message: {exc}")

    print(f"[cost] blocked reason={reason} info={info}")
    return False

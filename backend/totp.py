# totp.py
# ---------------------------------------------------------------------------
# SEGURANÇA (backlog: MFA): códigos de 6 dígitos do aplicativo autenticador
# (Google Authenticator, Microsoft Authenticator, 1Password, Authy...).
#
# Padrão TOTP (RFC 6238): o servidor e o aplicativo guardam o mesmo segredo e
# calculam, a cada 30 segundos, HMAC-SHA1(segredo, nº do intervalo) reduzido
# a 6 dígitos. Implementado aqui com a biblioteca padrão (hmac/hashlib) em vez
# de uma dependência a mais: são poucas linhas, cobertas pelos vetores de
# teste da própria RFC (tests/test_backend_routes.py).
#
# Regras:
#   - aceita o intervalo atual e 1 para cada lado (relógio do celular adiantado
#     ou atrasado em até 30 s);
#   - um código já usado não vale de novo (`last_step`): quem viu o código por
#     cima do ombro não consegue reaproveitá-lo nos segundos seguintes;
#   - códigos de recuperação (para quem perdeu o celular) são guardados só
#     como hash e valem uma vez cada.
# ---------------------------------------------------------------------------
import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD_SECONDS = 30
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10


def new_secret() -> str:
    """Segredo de 160 bits em Base32 (o formato que os aplicativos leem)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    normalized = secret.replace(" ", "").upper()
    return base64.b32decode(normalized + "=" * (-len(normalized) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // PERIOD_SECONDS)


def verify(secret: str, code: str, last_step: Optional[int] = None, now: Optional[float] = None) -> Optional[int]:
    """Devolve o intervalo do código, se ele for válido e ainda não usado; senão None."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(rf"\d{{{DIGITS}}}", code):
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if last_step is not None and candidate <= last_step:
            continue  # já usado (ou mais antigo que o último usado)
        # compare_digest: o tempo da comparação não revela quantos dígitos acertou.
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "PsiGestão") -> str:
    """Endereço otpauth:// que vira o QR code lido pelo aplicativo."""
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": PERIOD_SECONDS},
                      quote_via=quote)
    return f"otpauth://totp/{label}?{query}"


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Códigos como "k7m2-p9xq-4tbn-h3wd" (sem 0/O e 1/l, que se confundem).

    16 caracteres = ~79 bits: o banco guarda só o SHA-256 (rápido de conferir,
    sem bcrypt), e com essa entropia nem quem copiar o banco consegue
    descobrir os códigos testando combinações.
    """
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(16))
        codes.append("-".join(raw[i:i + 4] for i in range(0, 16, 4)))
    return codes


def hash_recovery_code(code: str) -> str:
    normalized = re.sub(r"[\s-]", "", (code or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

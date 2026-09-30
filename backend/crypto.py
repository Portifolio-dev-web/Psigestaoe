# crypto.py
# ---------------------------------------------------------------------------
# Gerenciamento das chaves de criptografia dos campos sensíveis
# (CPF, RG, endereço, conteúdo e diagnóstico dos prontuários).
#
# Algoritmo: Fernet = AES-128-CBC + HMAC-SHA256 (biblioteca `cryptography`).
#
# CORREÇÃO DE SEGURANÇA (item 2 da auditoria):
#   Antes, o servidor lia `FERNET_KEY`, mas o .env definia `ENCRYPTION_KEY`.
#   Como a variável certa nunca existia, a chave era derivada silenciosamente
#   do JWT_SECRET. Isso acoplava duas coisas que precisam ser independentes:
#   trocar o segredo de login tornaria TODOS os prontuários ilegíveis.
#
#   Agora:
#   - FERNET_KEY       -> chave própria, obrigatória em produção, usada para
#                         criptografar e descriptografar.
#   - FERNET_OLD_KEYS  -> (opcional) chaves antigas separadas por vírgula,
#                         usadas SOMENTE para descriptografar durante uma
#                         rotação de chave. Nunca criptografam dados novos.
#   A migração dos dados antigos é feita por `rotate_encryption_key.py`.
# ---------------------------------------------------------------------------
import base64
import hashlib
import logging
import os
import sys

from cryptography.fernet import Fernet, MultiFernet

logger = logging.getLogger("psigestao.crypto")

# Todo token Fernet começa com o byte de versão 0x80, que em base64 vira "gAAAAA".
# Usamos isso para diferenciar um valor criptografado de um texto puro legado.
FERNET_TOKEN_PREFIX = "gAAAAA"

# Chave fixa usada APENAS em desenvolvimento quando FERNET_KEY não é definida.
# Nunca é aceita em produção (ver load_fernet).
_DEV_FALLBACK_SEED = b"dev-secret-fernet-key-12345"


def legacy_key_from_secret(secret: str) -> bytes:
    """Reproduz a derivação antiga (SHA-256 do segredo -> base64).

    Era assim que a chave era gerada quando FERNET_KEY não existia (derivada
    do JWT_SECRET). Mantida apenas para o script de migração conseguir ler os
    dados antigos e recriptografá-los com a nova chave.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def dev_fallback_key() -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(_DEV_FALLBACK_SEED).digest())


def is_fernet_token(value: str) -> bool:
    return value.startswith(FERNET_TOKEN_PREFIX)


def _parse_key(raw_key: str, env_name: str) -> Fernet:
    """Valida a chave de verdade (instanciando o Fernet) em vez de só contar
    caracteres. Uma chave inválida derruba a inicialização: é melhor o servidor
    não subir do que gravar prontuários com uma chave errada."""
    try:
        return Fernet(raw_key.strip().encode("utf-8"))
    except (ValueError, TypeError):
        logger.critical(
            "SECURITY ALERT: %s inválida. Gere uma chave com: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
            env_name,
        )
        sys.exit(1)


def load_fernet(environment: str) -> MultiFernet:
    """Monta o MultiFernet usado pela aplicação.

    A primeira chave da lista é a única usada para CRIPTOGRAFAR; as demais
    servem só para DESCRIPTOGRAFAR (rotação de chave sem downtime).
    """
    raw_key = os.environ.get("FERNET_KEY", "").strip()

    if raw_key:
        primary = _parse_key(raw_key, "FERNET_KEY")
    elif environment == "production":
        # Fail-fast: em produção não existe fallback. Sem chave, sem servidor.
        logger.critical("SECURITY ALERT: FERNET_KEY ausente em produção. Abortando para proteger prontuários.")
        sys.exit(1)
    else:
        logger.warning("FERNET_KEY não definida: usando chave INSEGURA de desenvolvimento.")
        primary = Fernet(dev_fallback_key())

    old_keys = [
        _parse_key(k, "FERNET_OLD_KEYS")
        for k in os.environ.get("FERNET_OLD_KEYS", "").split(",")
        if k.strip()
    ]
    return MultiFernet([primary, *old_keys])

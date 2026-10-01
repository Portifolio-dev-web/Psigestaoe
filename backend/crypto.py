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
import hmac
import logging
import os
import re
import sys
import unicodedata

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


def hash_token(token: str) -> str:
    """Hash SHA-256 de um token de integração (item 15 da auditoria).

    O banco guarda só este hash, nunca o token. Para tokens ALEATÓRIOS de alta
    entropia (secrets.token_urlsafe(32) = 256 bits) um hash rápido é o
    adequado: não há o que adivinhar por força bruta, ao contrário de senhas,
    que precisam de bcrypt. Também é o que permite achar o dono do token com
    uma busca indexada (`WHERE webhook_token_hash = ...`).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


# ---------------------------------------------------------------------------
# Índice cego ("blind index") para buscar pacientes por nome
# (ESCALA: paginação e busca no servidor)
# ---------------------------------------------------------------------------
# O problema: o nome do paciente é criptografado (item 13), e cada
# criptografia gera um valor diferente. O banco não consegue fazer
# `WHERE nome LIKE 'mar%'`. Antes, a tela baixava TODOS os pacientes e
# filtrava no navegador; isso impede paginar e não escala.
#
# A solução: para cada palavra do nome guardamos o HMAC-SHA256 dos seus
# prefixos ("ma", "mar", "mari", "maria"), numa tabela à parte
# (patient_search_tokens). Na busca, calculamos o HMAC do que foi digitado
# e o banco compara HMAC com HMAC, usando índice.
#
# Propriedades e limites (documentados de propósito):
#   - Sem a SEARCH_INDEX_KEY, os tokens não revelam os nomes. Com a chave,
#     prefixos curtos podem ser testados por força bruta. Por isso a chave é
#     separada da FERNET_KEY e obrigatória em produção.
#   - O owner_id entra no HMAC: o mesmo nome em consultórios diferentes gera
#     tokens diferentes, então não dá para cruzar pacientes entre profissionais.
#   - Vaza apenas "estes dois pacientes do mesmo profissional têm nomes que
#     começam igual". É o preço da busca por prefixo; busca por palavra exata
#     vazaria menos, mas a experiência de digitar "mar" e achar "Maria" se perde.
#   - Trocar a SEARCH_INDEX_KEY exige reconstruir o índice
#     (python rebuild_search_index.py). Nenhum dado se perde: o índice é derivado.
SEARCH_MIN_PREFIX = 2   # prefixos de 1 letra vazariam a inicial e quase não filtram
SEARCH_MAX_PREFIX = 20  # nomes muito longos não precisam de prefixos maiores
_DEV_SEARCH_KEY = b"dev-search-index-key-insegura-0001"


def load_search_key(environment: str) -> bytes:
    raw_key = os.environ.get("SEARCH_INDEX_KEY", "").strip()
    if raw_key:
        if len(raw_key) < 32:
            logger.critical("SECURITY ALERT: SEARCH_INDEX_KEY deve ter pelo menos 32 caracteres aleatórios.")
            sys.exit(1)
        return raw_key.encode("utf-8")
    if environment == "production":
        # Com uma chave conhecida, os prefixos dos nomes poderiam ser descobertos.
        logger.critical("SECURITY ALERT: SEARCH_INDEX_KEY ausente em produção. Abortando.")
        sys.exit(1)
    logger.warning("SEARCH_INDEX_KEY não definida: usando chave INSEGURA de desenvolvimento.")
    return _DEV_SEARCH_KEY


def normalize_search_words(text: str) -> list:
    """Minúsculas, sem acento, só letras e números: "José da Silva" -> [jose, da, silva]."""
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", without_accents)


def _search_token(key: bytes, owner_id: str, fragment: str) -> str:
    digest = hmac.new(key, f"{owner_id}\x00{fragment}".encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:32]  # 128 bits bastam para não haver colisão e economizam espaço no índice


def name_search_tokens(key: bytes, owner_id: str, name: str) -> set:
    """Tokens gravados para um nome (todos os prefixos de cada palavra)."""
    tokens = set()
    for word in normalize_search_words(name):
        for size in range(SEARCH_MIN_PREFIX, min(len(word), SEARCH_MAX_PREFIX) + 1):
            tokens.add(_search_token(key, owner_id, word[:size]))
    return tokens


def query_search_tokens(key: bytes, owner_id: str, query: str) -> list:
    """Tokens da busca: um por palavra digitada (todas precisam bater)."""
    return [
        _search_token(key, owner_id, word[:SEARCH_MAX_PREFIX])
        for word in normalize_search_words(query)
        if len(word) >= SEARCH_MIN_PREFIX
    ]

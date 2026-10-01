# environment.py
# ---------------------------------------------------------------------------
# Em qual ambiente o sistema está rodando: "development" ou "production".
#
# SEGURANÇA (varredura, item 5: "inseguro por padrão"):
#   Antes, cada arquivo lia os.environ.get("ENVIRONMENT", "development"). Se o
#   deploy esquecesse a variável, ou errasse a grafia ("prod", "Production"),
#   o servidor subia calado em modo de DESENVOLVIMENTO: aceitava JWT_SECRET e
#   FERNET_KEY ausentes (usando valores escritos no código), mandava o cookie
#   de login sem `Secure`, deixava /docs e /metrics abertos e gravava os dados
#   num arquivo SQLite local.
#
#   Agora o padrão é o seguro (fail-closed): SÓ um "development" explícito
#   liga o modo de desenvolvimento. Variável ausente, vazia ou com qualquer
#   outro valor = produção, com todas as travas ligadas. O docker-compose.yml
#   (que é só de desenvolvimento) e os testes declaram "development"; em
#   produção não é preciso declarar nada.
#
# Todo arquivo que precisa saber o ambiente usa get_environment(), para a
# regra existir num lugar só.
# ---------------------------------------------------------------------------
import logging
import os

DEVELOPMENT = "development"
PRODUCTION = "production"

logger = logging.getLogger("psigestao.environment")
_warned_values: set[str] = set()


def get_environment() -> str:
    """Devolve "development" ou "production", nunca outro valor."""
    raw = os.environ.get("ENVIRONMENT", "").strip().lower()
    if raw == DEVELOPMENT:
        return DEVELOPMENT
    if raw and raw != PRODUCTION and raw not in _warned_values:
        # Valor desconhecido (provável erro de digitação): avisa uma vez e
        # segue como produção, nunca como desenvolvimento.
        _warned_values.add(raw)
        logger.warning("ENVIRONMENT=%r não é reconhecido: rodando como produção (padrão seguro).", raw)
    return PRODUCTION

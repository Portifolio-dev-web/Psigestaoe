# admin_access.py
# ---------------------------------------------------------------------------
# PRODUTO (administração): QUEM é administrador do PsiGestão.
#
# A lista vem SÓ da configuração do servidor (variável ADMIN_EMAILS, e-mails
# separados por vírgula), nunca do banco nem de uma rota. Assim:
#   - invadir uma conta comum, ou até o banco de dados, não cria administrador;
#   - nenhuma tela consegue "promover" alguém: só quem tem acesso ao servidor;
#   - tirar alguém da administração é apagar o e-mail da variável e reiniciar.
# Além de estar na lista, a conta precisa ter o e-mail COMPROVADO (link do
# cadastro, redefinição de senha ou Google) e não pode estar encerrada nem
# suspensa. A verificação em duas etapas é exigida nas rotas (server.py).
#
# Fail-closed: ADMIN_EMAILS com um item que não é e-mail impede a subida do
# servidor (um erro de digitação não pode deixar a administração num estado
# inesperado). Vazia = administração desligada (a tela não aparece para ninguém).
# ---------------------------------------------------------------------------
import logging
import os
import re

logger = logging.getLogger("psigestao.admin")

_EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def admin_emails() -> frozenset:
    """E-mails da administração (minúsculos). Lido a cada uso: barato, e os
    testes podem trocar a variável sem recarregar o módulo."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def is_admin(user) -> bool:
    """A conta é de administração? (e-mail na lista + e-mail comprovado + conta ativa)."""
    if user is None or not user.email:
        return False
    return (user.email.strip().lower() in admin_emails()
            and user.email_verified_at is not None
            and user.closed_at is None
            and getattr(user, "suspended_at", None) is None)


def validate_config(environment: str) -> None:
    invalid = [item for item in admin_emails() if not _EMAIL.match(item)]
    if invalid:
        raise SystemExit("ADMIN_EMAILS inválido: use e-mails separados por vírgula "
                         f"(ex.: dona@exemplo.com.br,socia@exemplo.com.br). Itens recusados: {len(invalid)}.")
    if not admin_emails():
        logger.info("Administração desligada: ADMIN_EMAILS vazio.")

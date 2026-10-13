# manage_billing.py
# ---------------------------------------------------------------------------
# PRODUTO (assinatura): cortesia e consulta da situação de uma conta, pela
# linha de comando do servidor.
#
# Por que um comando e não uma tela de administração: a cortesia libera o
# sistema sem pagamento. Uma rota na API seria mais uma porta de entrada a
# proteger (senha de administrador, 2FA, auditoria própria); o comando só roda
# para quem já tem acesso ao servidor, e cada uso fica na trilha de auditoria
# da conta ("sistema (linha de comando)").
#
# Uso (dentro do container da API):
#   python manage_billing.py cortesia conceder fulana@exemplo.com --motivo "Usuária fundadora"
#   python manage_billing.py cortesia revogar fulana@exemplo.com
#   python manage_billing.py situacao fulana@exemplo.com
#   python manage_billing.py admin verificar   (confere ADMIN_EMAILS; análise da administração, AD4)
# Em produção: docker compose exec api python manage_billing.py ...
# ---------------------------------------------------------------------------
import argparse
import asyncio
import difflib
import json
import sys

from sqlalchemy import func, select

import billing
import server
from models import User

STATE_LABELS = {
    "courtesy": "cortesia (acesso completo, sem cobrança)",
    "active": "assinante",
    "trialing": "teste grátis",
    "past_due": "pagamento em atraso (dentro da tolerância)",
    "read_only": "somente leitura",
}


def describe(state: dict) -> str:
    """Resumo legível da situação (sem dados pessoais além do que foi digitado)."""
    lines = [f"Situação: {STATE_LABELS.get(state['state'], state['state'])}"]
    if state.get("reason"):
        lines.append(f"Motivo do somente leitura: {state['reason']}")
    if state.get("plan"):
        lines.append(f"Plano: {billing.PLAN_LABELS.get(state['plan'], state['plan'])}")
    for key, label in (("trial_ends_at", "Fim do teste"), ("paid_until", "Pago até"), ("grace_ends_at", "Tolerância até")):
        if state.get(key):
            lines.append(f"{label}: {state[key]}")
    lines.append(f"Pode registrar (criar/editar): {'sim' if state['can_write'] else 'não'}")
    return "\n".join(lines)


def _yes(flag: bool) -> str:
    return "sim" if flag else "NÃO"


async def check_admins(db) -> int:
    """AD4: para cada e-mail de ADMIN_EMAILS, o que falta para a administração
    aparecer. Quando não há conta, sugere um e-mail cadastrado PARECIDO (o erro
    mais comum é de digitação, no cadastro ou na variável). Roda no console do
    servidor: quem vê a saída já tem acesso ao banco."""
    report = await server.admin_config_report(db)
    if not report:
        print("ADMIN_EMAILS está vazio: a administração está desligada.")
        return 1
    all_emails = None
    for item in report:
        print(f"{item['email']}: {'PRONTO' if item['ready'] else 'NÃO ESTÁ PRONTO'}")
        if not item["account"]:
            if all_emails is None:
                all_emails = [e.lower() for e in (await db.execute(select(User.email))).scalars().all()]
            similar = difflib.get_close_matches(item["email"], all_emails, n=1, cutoff=0.85)
            hint = f" Existe uma conta parecida: {similar[0]} (erro de digitação?)" if similar else ""
            print(f"  - conta: não existe. Cadastre-se com este e-mail.{hint}")
            continue
        print(f"  - e-mail comprovado: {_yes(item['email_verified'])}")
        print(f"  - verificação em duas etapas: {_yes(item['mfa_enabled'])}"
              + ("" if item["mfa_enabled"] else " (ative em Configurações > Minha conta)"))
        print(f"  - conta ativa (não encerrada nem suspensa): {_yes(item['active'])}")
    print("Mudou ADMIN_EMAILS? Reinicie a API (docker compose up -d api).")
    return 0 if all(item["ready"] for item in report) else 1


async def run(args) -> int:
    async with server.AsyncSessionLocal() as db:
        if args.command == "admin":
            return await check_admins(db)
        if args.command == "cortesia":
            try:
                state = await server.set_courtesy(db, args.email, args.acao == "conceder", args.motivo or "")
            except LookupError as error:
                print(error, file=sys.stderr)
                return 1
            print(("Cortesia concedida." if args.acao == "conceder" else "Cortesia revogada.") + "\n" + describe(state))
            return 0
        user = (await db.execute(select(User).where(func.lower(User.email) == args.email.strip().lower()))).scalar_one_or_none()
        if user is None:
            print(f"Nenhuma conta com o e-mail {args.email}.", file=sys.stderr)
            return 1
        state = billing.access_state(user)
        print(json.dumps(state, ensure_ascii=False, indent=1) if args.json else describe(state))
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Assinatura: cortesia e situação de uma conta.")
    commands = parser.add_subparsers(dest="command", required=True)
    courtesy = commands.add_parser("cortesia", help="conceder ou revogar a cortesia")
    courtesy.add_argument("acao", choices=["conceder", "revogar"])
    courtesy.add_argument("email")
    courtesy.add_argument("--motivo", help="motivo curto (até 120 caracteres), ex.: usuária fundadora")
    status = commands.add_parser("situacao", help="mostra a situação da assinatura de uma conta")
    status.add_argument("email")
    status.add_argument("--json", action="store_true", help="saída em JSON")
    admin = commands.add_parser("admin", help="confere ADMIN_EMAILS (conta, e-mail comprovado, duas etapas)")
    admin.add_argument("acao", choices=["verificar"])
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()

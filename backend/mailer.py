# mailer.py
# ---------------------------------------------------------------------------
# Envio de e-mails (SEGURANÇA MÉDIA e backlog).
#
# Antes o sistema não mandava e-mail nenhum, e por isso não existia:
#   - "Esqueci minha senha" (quem esquecia a senha perdia a conta);
#   - confirmação do e-mail no cadastro (qualquer um criava uma conta com o
#     e-mail de outra pessoa, e o cadastro revelava quais e-mails têm conta);
#   - aviso ao dono da conta quando algo sensível muda (senha, verificação em
#     duas etapas, encerramento da conta).
#
# Destinos possíveis (MAIL_BACKEND):
#   - "smtp": servidor SMTP de verdade (SMTP_HOST etc.). OBRIGATÓRIO em
#     produção, com conexão criptografada (STARTTLS ou SSL);
#   - "console": só no desenvolvimento, quando não há SMTP configurado. O
#     e-mail (com o link) aparece no log da API. Em produção nunca: o log
#     teria links de troca de senha;
#   - "memory": usado pelos testes automatizados (lista `outbox`).
# No docker-compose de desenvolvimento os e-mails vão para o Mailpit
# (http://localhost:8025), que mostra a caixa de entrada de mentira.
#
# O envio roda numa thread (o smtplib é bloqueante) e quem chama decide se
# espera: as rotas públicas (cadastro, "esqueci minha senha") mandam o e-mail
# DEPOIS de responder, para o tempo de resposta não revelar se o e-mail tem conta.
# ---------------------------------------------------------------------------
import asyncio
import logging
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from typing import Optional

from environment import PRODUCTION, get_environment

logger = logging.getLogger("psigestao.mailer")

APP_NAME = "PsiGestão"


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


# Caixa de saída do destino "memory" (testes).
outbox: list[OutgoingEmail] = []


def _setting(name: str, default: str = "") -> str:
    # Lido a cada envio (e não na importação): os testes trocam as variáveis.
    return os.environ.get(name, default).strip()


def mail_backend() -> str:
    explicit = _setting("MAIL_BACKEND").lower()
    if explicit:
        return explicit
    return "smtp" if _setting("SMTP_HOST") else "console"


def check_mail_config(environment: str) -> None:
    """Em produção o servidor não sobe sem SMTP (fail-closed, como o Redis).

    Sem e-mail, ninguém consegue concluir o cadastro nem recuperar a senha;
    subir assim seria descobrir o problema pela reclamação de um usuário.
    """
    if environment != PRODUCTION:
        return
    problems = []
    if mail_backend() != "smtp":
        problems.append("MAIL_BACKEND precisa ser smtp")
    if not _setting("SMTP_HOST"):
        problems.append("defina SMTP_HOST")
    if not _setting("SMTP_FROM"):
        problems.append("defina SMTP_FROM (ex.: PsiGestão <nao-responda@seudominio.com.br>)")
    if _setting("SMTP_SECURITY", "starttls").lower() not in ("starttls", "ssl"):
        # Sem criptografia, a senha do SMTP e os links de troca de senha
        # passariam em texto puro pela rede.
        problems.append("SMTP_SECURITY precisa ser starttls ou ssl")
    if problems:
        logger.critical("SECURITY ALERT: envio de e-mail mal configurado em produção (%s). Abortando.",
                        "; ".join(problems))
        sys.exit(1)


def _build_message(mail: OutgoingEmail) -> EmailMessage:
    message = EmailMessage()
    sender = _setting("SMTP_FROM") or f"{APP_NAME} <nao-responda@localhost>"
    message["From"] = sender
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip("> ") or "localhost")
    # E-mails automáticos: pede aos servidores que não respondam com "estou de férias".
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(mail.text)
    message.add_alternative(mail.html, subtype="html")
    return message


def _send_smtp(message: EmailMessage) -> None:
    host = _setting("SMTP_HOST")
    security = _setting("SMTP_SECURITY", "starttls").lower()
    port = int(_setting("SMTP_PORT") or (465 if security == "ssl" else 587))
    username, password = _setting("SMTP_USERNAME"), os.environ.get("SMTP_PASSWORD", "")
    timeout = 15
    context = ssl.create_default_context()  # confere o certificado do servidor SMTP
    if security == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
    with client:
        if security == "starttls":
            client.starttls(context=context)
        if username:
            client.login(username, password)
        client.send_message(message)


async def send_email(mail: OutgoingEmail) -> bool:
    """Envia o e-mail. Devolve False se falhar (o erro vai para o log, sem o
    destinatário: o log não precisa de dados pessoais para avisar da falha)."""
    backend = mail_backend()
    if backend == "memory":
        outbox.append(mail)
        return True
    if backend == "console":
        if get_environment() == PRODUCTION:
            logger.error("E-mail não enviado: destino 'console' não é permitido em produção (%s).", mail.subject)
            return False
        logger.info("E-mail (desenvolvimento, sem SMTP) para %s | %s\n%s", mail.to, mail.subject, mail.text)
        return True
    try:
        await asyncio.to_thread(_send_smtp, _build_message(mail))
        return True
    except Exception:
        logger.exception("Falha ao enviar e-mail: %s", mail.subject)
        return False


# ---------------------------------------------------------------------------
# Modelos das mensagens
# ---------------------------------------------------------------------------
# Texto puro + HTML simples (estilos inline: muitos leitores de e-mail
# ignoram <style>). Tudo o que vem de fora (nome, links) é escapado no HTML.
# Nenhum e-mail leva dado de paciente.

def _html(title: str, paragraphs: list[str], button: Optional[tuple[str, str]] = None,
          footer: str = "") -> str:
    parts = [f'<p style="margin:0 0 14px;color:#334155;font-size:15px;line-height:1.55">{p}</p>' for p in paragraphs]
    if button:
        label, url = button
        parts.append(
            f'<p style="margin:22px 0"><a href="{escape(url, quote=True)}" '
            'style="background:#1E3A8A;color:#ffffff;text-decoration:none;padding:12px 20px;'
            f'border-radius:8px;font-weight:600;display:inline-block">{escape(label)}</a></p>'
            '<p style="margin:0 0 14px;color:#64748B;font-size:13px">Se o botão não funcionar, copie este '
            f'endereço no navegador:<br><span style="word-break:break-all">{escape(url)}</span></p>'
        )
    if footer:
        parts.append(f'<p style="margin:18px 0 0;color:#64748B;font-size:13px">{footer}</p>')
    return (
        '<div style="background:#F0F4F8;padding:24px 12px;font-family:Arial,Helvetica,sans-serif">'
        '<div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px">'
        f'<p style="margin:0 0 6px;color:#1E3A8A;font-weight:700;font-size:14px">{APP_NAME}</p>'
        f'<h1 style="margin:0 0 18px;color:#0F172A;font-size:20px">{escape(title)}</h1>'
        + "".join(parts) +
        "</div></div>"
    )


def _greeting(name: str) -> str:
    return f"Olá, {name.split()[0]}." if name and name.strip() else "Olá."


def signup_confirmation(to: str, name: str, link: str, hours: int) -> OutgoingEmail:
    greeting = _greeting(name)
    text = (f"{greeting}\n\nPara concluir seu cadastro no {APP_NAME}, abra o link abaixo e crie sua senha "
            f"(vale por {hours} horas):\n\n{link}\n\n"
            "Se não foi você que pediu o cadastro, ignore este e-mail: nenhuma conta será criada.")
    html = _html("Confirme seu e-mail", [
        escape(greeting),
        f"Para concluir seu cadastro no {APP_NAME}, confirme este e-mail e crie sua senha. "
        f"O link vale por {hours} horas.",
    ], ("Confirmar e criar senha", link),
        "Se não foi você que pediu o cadastro, ignore este e-mail: nenhuma conta será criada.")
    return OutgoingEmail(to, f"Confirme seu e-mail no {APP_NAME}", text, html)


def signup_existing_account(to: str, login_link: str, forgot_link: str) -> OutgoingEmail:
    text = (f"Recebemos um pedido de cadastro no {APP_NAME} com este e-mail, mas ele já tem uma conta.\n\n"
            f"Para entrar: {login_link}\nSe esqueceu a senha: {forgot_link}\n\n"
            "Se não foi você, ignore este e-mail. Sua conta continua como está.")
    html = _html("Você já tem uma conta", [
        f"Recebemos um pedido de cadastro no {APP_NAME} com este e-mail, mas ele já tem uma conta.",
        f'Se esqueceu a senha, <a href="{escape(forgot_link, quote=True)}" style="color:#1E3A8A">'
        "crie uma nova por aqui</a>.",
    ], ("Entrar", login_link), "Se não foi você, ignore este e-mail. Sua conta continua como está.")
    return OutgoingEmail(to, f"Sua conta no {APP_NAME}", text, html)


def password_reset(to: str, name: str, link: str, minutes: int, has_password: bool) -> OutgoingEmail:
    greeting = _greeting(name)
    action = "redefinir sua senha" if has_password else "criar uma senha (hoje você entra pelo Google)"
    text = (f"{greeting}\n\nRecebemos um pedido para {action} no {APP_NAME}. "
            f"Abra o link abaixo (vale por {minutes} minutos e só pode ser usado uma vez):\n\n{link}\n\n"
            "Se não foi você, ignore este e-mail: sua senha continua a mesma.")
    html = _html("Redefinição de senha", [
        escape(greeting),
        f"Recebemos um pedido para {escape(action)} no {APP_NAME}. O link vale por {minutes} minutos "
        "e só pode ser usado uma vez.",
    ], ("Criar nova senha", link), "Se não foi você, ignore este e-mail: sua senha continua a mesma.")
    return OutgoingEmail(to, f"Redefinição de senha do {APP_NAME}", text, html)


def security_notice(to: str, name: str, subject: str, what: str, when: str, where: str) -> OutgoingEmail:
    """Aviso de mudança sensível na conta (senha, verificação em duas etapas)."""
    greeting = _greeting(name)
    text = (f"{greeting}\n\n{what}\n\nQuando: {when}\nOnde: {where}\n\n"
            "Se não foi você, entre no sistema, troque a senha e use \"Sair dos outros dispositivos\" "
            "em Configurações > Minha conta.")
    html = _html(subject, [
        escape(greeting), escape(what),
        f"<b>Quando:</b> {escape(when)}<br><b>Onde:</b> {escape(where)}",
    ], footer="Se não foi você, entre no sistema, troque a senha e use \"Sair dos outros dispositivos\" "
              "em Configurações &gt; Minha conta.")
    return OutgoingEmail(to, f"{subject} | {APP_NAME}", text, html)


def account_closed(to: str, name: str, deleted: int, retained: int, retained_until: Optional[str]) -> OutgoingEmail:
    greeting = _greeting(name)
    lines = [f"Sua conta no {APP_NAME} foi encerrada. Ninguém consegue mais entrar nela.",
             f"Pacientes excluídos definitivamente: {deleted}."]
    if retained:
        lines.append(
            f"Pacientes com prontuário sob guarda obrigatória (Resolução CFP nº 001/2009): {retained}. "
            f"Eles continuam criptografados, sem acesso de ninguém, e serão excluídos automaticamente "
            f"em {retained_until}.")
    lines.append("Se você não pediu o encerramento, responda a este e-mail ou procure o suporte imediatamente.")
    text = f"{greeting}\n\n" + "\n\n".join(lines)
    html = _html("Conta encerrada", [escape(greeting)] + [escape(line) for line in lines])
    return OutgoingEmail(to, f"Conta encerrada no {APP_NAME}", text, html)


def webhook_token_expiring(to: str, name: str, expires_on: str, link: str) -> OutgoingEmail:
    greeting = _greeting(name)
    text = (f"{greeting}\n\nO token da integração com o Google Forms vence em {expires_on}. Depois disso, "
            "o formulário deixa de cadastrar pacientes no sistema.\n\nPara continuar, gere um token novo em "
            f"Pacientes > Integrar Google Forms e atualize o script do formulário:\n{link}")
    html = _html("Token do Google Forms perto do vencimento", [
        escape(greeting),
        f"O token da integração com o Google Forms vence em <b>{escape(expires_on)}</b>. Depois disso, "
        "o formulário deixa de cadastrar pacientes no sistema.",
        "Para continuar, gere um token novo em Pacientes &gt; Integrar Google Forms e atualize o script do formulário.",
    ], ("Abrir o PsiGestão", link))
    return OutgoingEmail(to, f"Token do Google Forms vence em {expires_on}", text, html)

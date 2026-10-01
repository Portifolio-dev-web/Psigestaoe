# proxy_config.py
# ---------------------------------------------------------------------------
# Configuração do proxy na frente da API (SEGURANÇA, varredura item 8).
#
# Fica num módulo próprio, só com a biblioteca padrão, porque é lida pelo
# gunicorn.conf.py (que só roda em Linux) e precisa ser testável em qualquer
# máquina (tests/test_backend_routes.py).
# ---------------------------------------------------------------------------
import ipaddress
import os


def forwarded_allow_ips_from_env() -> str:
    """De quem aceitar o cabeçalho X-Forwarded-For (o IP real do usuário).

    SEGURANÇA (varredura, item 8): antes o padrão silencioso era "127.0.0.1".
    Atrás de um proxy que não fica na mesma máquina (load balancer, rede do
    Docker, plataforma de hospedagem), TODO acesso parecia vir do IP do proxy:
      - 10 logins por minuto, somando todos os usuários, travavam o login de
        todo mundo (o limite é por IP);
      - a trilha de auditoria gravava o IP do proxy, não o de quem agiu.
    No outro extremo, "*" sem proxy na frente deixa qualquer um FORJAR o IP
    pelo cabeçalho e escapar do limite.

    Agora, em produção, FORWARDED_ALLOW_IPS é OBRIGATÓRIO e validado:
      - "10.0.0.5,10.0.0.6": IPs exatos do(s) proxy(s) (uvicorn 0.25 não aceita faixas);
      - "*": SÓ quando a porta da API não é acessível de fora, apenas pelo
        proxy da plataforma (ex.: Render, Railway, Fly), cujo IP muda;
      - "127.0.0.1": não há proxy na frente (o cabeçalho de qualquer outro
        endereço é ignorado).
    Só valores que o gunicorn também aceita: ele lê esta mesma variável antes
    deste arquivo e não sobe com nada além de IPs ou "*".
    No desenvolvimento, sem a variável, vale "127.0.0.1" como antes.
    """
    raw = os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    if not raw:
        # Mesma regra de environment.py: só "development" explícito é desenvolvimento.
        if os.environ.get("ENVIRONMENT", "").strip().lower() != "development":
            raise SystemExit(
                "SECURITY ALERT: defina FORWARDED_ALLOW_IPS em produção: o(s) IP(s) do proxy, "
                "'*' (porta só acessível pelo proxy da plataforma) ou '127.0.0.1' (sem proxy).")
        return "127.0.0.1"
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if "*" in entries:
        if len(entries) > 1:
            raise SystemExit("FORWARDED_ALLOW_IPS: use '*' sozinho ou uma lista de IPs, não os dois.")
        return "*"
    for entry in entries:
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            raise SystemExit(f"FORWARDED_ALLOW_IPS: '{entry}' não é um IP válido "
                             "(faixas como 10.0.0.0/8 não são aceitas por esta versão do uvicorn).") from None
    return ",".join(entries)

# maintenance.py
# ---------------------------------------------------------------------------
# Rotina de manutenção, para rodar por um agendador externo (cron, tarefa
# agendada da plataforma de hospedagem):
#
#     docker compose run --rm api python maintenance.py
#
# Faz o mesmo que a API já faz sozinha a cada MAINTENANCE_INTERVAL_HOURS
# (server.run_maintenance):
#   - exclui o que sobrou das contas ENCERRADAS quando termina a guarda legal
#     (prontuários: 5 anos, Resolução CFP nº 001/2009; registros de acesso:
#     6 meses, Marco Civil da Internet);
#   - apaga os links de cadastro do paciente encerrados há mais de 30 dias
#     (os incompletos ficam até a psicóloga completar ou descartar).
# Útil quando a API fica desligada por longos períodos ou quando
# MAINTENANCE_ENABLED=false. Pode rodar junto com a API: uma trava no banco
# garante que só um processo execute a rotina por vez.
# ---------------------------------------------------------------------------
import asyncio
import json

import server


def main() -> None:
    result = asyncio.run(server.run_maintenance())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

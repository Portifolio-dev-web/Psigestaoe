"""Filtro do `yarn audit` no CI (segurança média: CI).

Uso: yarn audit --groups dependencies --json > audit.json; python yarn_audit_gate.py audit.json

Regra:
  - FALHA com qualquer vulnerabilidade em pacote que vai para o navegador
    (React, Radix, date-fns...) ou com qualquer vulnerabilidade CRÍTICA;
  - só RELATA as que estão dentro do `react-scripts` (ferramenta de build do
    Create React App). Elas não chegam à produção, onde o nginx serve só os
    arquivos estáticos já compilados, e somem na migração para o Vite (backlog).
Sem este filtro, o CI ficaria sempre vermelho por causa do react-scripts, e
um alerta que sempre toca deixa de ser lido.
"""
import json
import sys
from collections import Counter

BUILD_ONLY = {"react-scripts"}


def main(path: str) -> int:
    blocking, reported = [], Counter()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") != "auditAdvisory":
                continue
            advisory = item["data"]["advisory"]
            top_level = item["data"]["resolution"]["path"].split(">")[0]
            severity = advisory["severity"]
            if top_level in BUILD_ONLY and severity != "critical":
                reported[severity] += 1
                continue
            blocking.append(f"{severity:9} {advisory['module_name']} (via {item['data']['resolution']['path']}): "
                            f"{advisory['title']} {advisory.get('url', '')}")

    if reported:
        summary = ", ".join(f"{count} {severity}" for severity, count in sorted(reported.items()))
        print(f"Só no build (react-scripts, fora da produção): {summary}. Some na migração para o Vite.")
    if blocking:
        print("Vulnerabilidades que BLOQUEIAM (pacotes que vão para o navegador, ou críticas):")
        for line in sorted(set(blocking)):
            print("  " + line)
        return 1
    print("Nenhuma vulnerabilidade em pacote que vai para a produção.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

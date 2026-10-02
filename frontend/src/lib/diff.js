// ---------------------------------------------------------------------------
// UX (histórico de versões da evolução): o que mudou entre duas versões.
//
// Compara os textos LINHA A LINHA (cada parágrafo da evolução é uma linha) com
// o algoritmo da maior subsequência comum (LCS): o resultado diz quais linhas
// ficaram iguais, quais saíram e quais entraram, na ordem do texto.
// Textos enormes (mais de `maxLines` linhas) não são comparados (o custo cresce
// com linhas × linhas); a tela mostra só o texto completo nesse caso.
// ---------------------------------------------------------------------------

/** [{ type: "same" | "removed" | "added", text }] ou null se grande demais. */
export function diffLines(before, after, maxLines = 1500) {
  const a = String(before ?? "").split("\n");
  const b = String(after ?? "").split("\n");
  if (a.length > maxLines || b.length > maxLines) return null;

  // lcs[i][j] = tamanho da maior subsequência comum entre a[i..] e b[j..].
  const lcs = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const result = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      result.push({ type: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      result.push({ type: "removed", text: a[i] });
      i += 1;
    } else {
      result.push({ type: "added", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) result.push({ type: "removed", text: a[i++] });
  while (j < b.length) result.push({ type: "added", text: b[j++] });
  return result;
}

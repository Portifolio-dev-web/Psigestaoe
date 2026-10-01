import api from "@/lib/api";

// ---------------------------------------------------------------------------
// PRODUTO: rascunhos automáticos de prontuário, criptografados no navegador.
//
// O problema: uma evolução clínica pode levar 40 minutos para ser escrita.
// Sessão expirada, queda de rede, aba fechada sem querer ou travamento do
// navegador e todo o texto se perdia.
//
// A solução: enquanto a pessoa escreve, o texto é salvo NESTE computador a
// cada pausa na digitação. Como é dado de saúde, nunca fica em texto puro:
//   - é criptografado com AES-GCM (Web Crypto, nativo do navegador);
//   - a chave vem do servidor (GET /auth/draft-key) e só existe na MEMÓRIA
//     enquanto há login. No disco fica só o texto cifrado: quem copiar o
//     armazenamento do navegador não consegue ler sem entrar na conta;
//   - cada rascunho é "amarrado" ao seu lugar (dado adicional autenticado):
//     não dá para trocar o rascunho de um paciente pelo de outro;
//   - rascunhos vencem em 7 dias (minimização de dados, LGPD) e somem ao
//     salvar a anotação. Em Configurações é possível invalidar os rascunhos
//     de TODOS os aparelhos (ex.: notebook perdido), trocando a chave.
// Sem Web Crypto (navegador antigo ou página sem HTTPS), o rascunho fica
// DESLIGADO: preferimos não guardar a guardar em texto puro.
// ---------------------------------------------------------------------------

const PREFIX = "psi-draft:v1:";
const TTL_MS = 7 * 24 * 60 * 60 * 1000;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

// A chave fica em memória associada ao DONO. Se outra pessoa entrar na mesma
// aba, a chave é buscada de novo (nunca se usa a chave de outro usuário).
let keyPromise = null;
let keyOwner = null;

function storage() {
  try {
    return window.localStorage;
  } catch (e) {
    return null; // modo privado/bloqueado: sem rascunho
  }
}

export function draftsSupported() {
  return Boolean(window.isSecureContext && window.crypto?.subtle && storage());
}

// Em blocos: `String.fromCharCode(...bytes)` de uma vez estoura a pilha com
// textos longos (uma evolução clínica pode ter dezenas de KB).
const toBase64 = (bytes) => {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
};
const fromBase64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

const ownerOf = (slot) => slot.slice(PREFIX.length).split(":")[0];

async function getKey(owner) {
  if (!keyPromise || keyOwner !== owner) {
    keyOwner = owner;
    const request = api.get("/auth/draft-key").then(({ data }) =>
      window.crypto.subtle.importKey("raw", fromBase64(data.key), { name: "AES-GCM" }, false, ["encrypt", "decrypt"]),
    );
    keyPromise = request;
    // Falhou (ex.: sem rede, tela bloqueada): tenta de novo na próxima vez.
    request.catch(() => { if (keyPromise === request) keyPromise = null; });
  }
  return keyPromise;
}

/** Busca a chave AO ABRIR o editor. Assim ela já está em memória se a
 * sessão expirar ou a tela bloquear no meio do texto: o rascunho ainda pode
 * ser gravado nesse momento, que é justamente quando ele mais importa. */
export function prepareDraftKey(userId) {
  if (draftsSupported()) getKey(userId).catch(() => {});
}

/** Esquece a chave (logout EXPLÍCITO ou chave trocada). Na sessão expirada a
 * chave é mantida de propósito: o texto também ainda está na memória da aba,
 * e sem a chave o último rascunho não poderia ser gravado. */
export function forgetDraftKey() {
  keyPromise = null;
  keyOwner = null;
}

/** Identificador do rascunho: por usuário, tipo e alvo (ex.: nova anotação do paciente X). */
export function draftSlot(userId, kind, targetId) {
  return `${PREFIX}${userId}:${kind}:${targetId}`;
}

/** Só os metadados (quando foi salvo), sem descriptografar: para avisos rápidos na tela. */
export function draftInfo(slot) {
  try {
    const raw = storage()?.getItem(slot);
    if (!raw) return null;
    const { savedAt } = JSON.parse(raw);
    if (Date.now() - savedAt > TTL_MS) {
      storage().removeItem(slot);
      return null;
    }
    return { savedAt };
  } catch (e) {
    return null;
  }
}

export async function saveDraft(slot, payload) {
  const key = await getKey(ownerOf(slot));
  const iv = window.crypto.getRandomValues(new Uint8Array(12)); // nunca reutilizar IV no AES-GCM
  const ciphertext = await window.crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode(slot) },
    key,
    encoder.encode(JSON.stringify(payload)),
  );
  const savedAt = Date.now();
  storage().setItem(slot, JSON.stringify({ v: 1, savedAt, iv: toBase64(iv), ct: toBase64(new Uint8Array(ciphertext)) }));
  return savedAt;
}

/** Devolve {payload, savedAt} ou null. Rascunho que não abre (chave trocada) é apagado. */
export async function readDraft(slot) {
  const raw = storage()?.getItem(slot);
  if (!raw) return null;
  try {
    const record = JSON.parse(raw);
    if (Date.now() - record.savedAt > TTL_MS) throw new Error("vencido");
    const key = await getKey(ownerOf(slot));
    const plaintext = await window.crypto.subtle.decrypt(
      { name: "AES-GCM", iv: fromBase64(record.iv), additionalData: encoder.encode(slot) },
      key,
      fromBase64(record.ct),
    );
    return { payload: JSON.parse(decoder.decode(plaintext)), savedAt: record.savedAt };
  } catch (e) {
    storage()?.removeItem(slot);
    return null;
  }
}

export function deleteDraft(slot) {
  try {
    storage()?.removeItem(slot);
  } catch (e) {
    // nada a fazer
  }
}

/** Apaga rascunhos vencidos (de qualquer usuário deste navegador). */
export function purgeExpiredDrafts() {
  const store = storage();
  if (!store) return;
  Object.keys(store)
    .filter((name) => name.startsWith(PREFIX))
    .forEach((name) => {
      try {
        if (Date.now() - JSON.parse(store.getItem(name)).savedAt > TTL_MS) store.removeItem(name);
      } catch (e) {
        store.removeItem(name); // formato desconhecido: descarta
      }
    });
}

/** Apaga os rascunhos deste usuário neste navegador (após invalidar a chave). */
export function clearUserDrafts(userId) {
  const store = storage();
  if (!store) return;
  Object.keys(store)
    .filter((name) => name.startsWith(`${PREFIX}${userId}:`))
    .forEach((name) => store.removeItem(name));
}

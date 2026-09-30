"""Cliente LLM único do CrossCheckBR: OpenRouter (se houver chave) → LLM local.

Toda geração do pipeline passa por aqui (afirmações, juiz, padrões), para que
os três tenham o mesmo comportamento de provedor, retry e telemetria:

- `chat_json(messages, schema, finalidade, ...)`: pede JSON, valida com Pydantic
  e, se vier inválido, faz **um** retry com mensagem de correção (o erro de
  validação vai para o modelo). Nunca levanta: devolve `ResultadoLLM(ok=False)`.
- `chat_texto(messages, finalidade, ...)`: texto livre, mesmo roteamento.

Provedores, nesta ordem:
1. OpenRouter (`llm_openrouter.chat`, teto diário) — só com `OPENROUTER_API_KEY`.
2. LLM local OpenAI-compatível (Unsloth Studio em `UNSLOTH_BASE_URL`). O modelo
   é `UNSLOTH_MODEL_NAME` ou, vazio, o que `/models` marca com `loaded: true`
   (nunca carrega outro modelo: só usa o que já está no Studio).

I/O sempre via `replay.llm_post`/`replay.http_get` (cassetes + evento `llm` com
`finalidade`). Cada provedor que falha emite `telemetria.fallback("llm.<prov>")`.
Temperatura 0. O modelo local atual (LFM2.5-8B-A1B) é de raciocínio e ignora
`enable_thinking`/`reasoning_budget`; com `LLM_LOCAL_RACIOCINIO=0` (padrão) a
conversa ganha um turno de assistente com `<think></think>` vazio (prefill), que
pula o raciocínio. Os `max_tokens` padrão continuam folgados para o caso =1.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pydantic import TypeAdapter, ValidationError

from . import config, replay, telemetria

SISTEMA_PT = ("Você é um assistente de checagem de fatos. Responda sempre em português do Brasil. "
              "Quando pedirem JSON, responda somente com JSON válido, sem markdown e sem texto extra.")

PREFILL_SEM_RACIOCINIO = "<think>\n\n</think>\n\n"

_modelo_local: Optional[str] = None
_trava = threading.Lock()


class ErroLLM(RuntimeError):
    """Falha de um provedor (rede, HTTP, resposta vazia/truncada, JSON inválido)."""


@dataclass
class ResultadoLLM:
    ok: bool
    dados: Any = None
    texto: str = ""
    motor: str = ""        # openrouter | llm-local
    modelo: str = ""
    erro: Optional[str] = None
    tentativas: int = 0
    erros_provedor: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- modelo local
def resetar_cache() -> None:
    global _modelo_local
    with _trava:
        _modelo_local = None


def modelo_local() -> str:
    """Nome do modelo local: `UNSLOTH_MODEL_NAME` ou o `loaded: true` de /models."""
    global _modelo_local
    if config.UNSLOTH_MODEL_NAME:
        return config.UNSLOTH_MODEL_NAME
    with _trava:
        if _modelo_local:
            return _modelo_local
    r = replay.http_get(config.UNSLOTH_BASE_URL.rstrip("/") + "/models", timeout=15)
    r.raise_for_status()
    itens = (r.json() or {}).get("data", []) or []
    carregados = [m for m in itens if isinstance(m, dict) and m.get("loaded") is True]
    if carregados:
        nome = carregados[0].get("id", "")
    else:
        nome = (itens[0] or {}).get("id", "") if itens else ""
        if nome:
            telemetria.fallback("llm.modelo-local", "nenhum modelo com loaded=true em /models: usando o 1º",
                                modelo=nome)
    if not nome:
        raise ErroLLM("sem modelo local em /models")
    with _trava:
        _modelo_local = nome
    return nome


def _local(messages: List[dict], max_tokens: int, timeout_s: float, finalidade: str) -> tuple:
    modelo = modelo_local()
    if not getattr(config, "LLM_LOCAL_RACIOCINIO", False):
        # O modelo local (LFM2.5-8B-A1B) é de raciocínio e ignora enable_thinking/reasoning_budget:
        # um bloco <think> vazio pré-preenchido pula o raciocínio (juiz: 160-240 s -> segundos).
        messages = list(messages) + [{"role": "assistant", "content": PREFILL_SEM_RACIOCINIO}]
    payload = {"model": modelo, "messages": messages, "temperature": 0.0, "max_tokens": max_tokens}
    r = replay.llm_post(config.UNSLOTH_BASE_URL.rstrip("/") + "/chat/completions", payload,
                        timeout=timeout_s, motor="llm-local", finalidade=finalidade)
    if r.status_code >= 400:
        raise ErroLLM(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json() or {}
    ch = (d.get("choices") or [{}])[0] or {}
    texto = ((ch.get("message") or {}).get("content") or "").strip()
    if not texto:
        fim = ch.get("finish_reason")
        raise ErroLLM("resposta vazia" + (" (truncada: finish_reason=length; o raciocínio consumiu "
                                           "os max_tokens)" if fim == "length" else ""))
    return texto, modelo


def _openrouter(messages: List[dict], max_tokens: int, timeout_s: float, finalidade: str) -> tuple:
    from . import llm_openrouter
    # O juiz pode usar um modelo próprio (OPENROUTER_MODEL_JUIZ); se falhar, cai no modelo global.
    modelos = None
    if finalidade == "juiz" and getattr(config, "OPENROUTER_MODEL_JUIZ", ""):
        modelos = [m for m in (config.OPENROUTER_MODEL_JUIZ, config.OPENROUTER_MODEL,
                               config.OPENROUTER_FALLBACK_MODEL) if m]
    with telemetria.finalidade(finalidade):
        return llm_openrouter.chat(messages, max_tokens=max_tokens, timeout_s=int(timeout_s),
                                   modelos=modelos)


def _provedores() -> List[tuple]:
    provs = []
    if config.OPENROUTER_API_KEY:
        provs.append(("openrouter", _openrouter))
    provs.append(("llm-local", _local))
    return provs


def _com_sistema(messages: List[dict]) -> List[dict]:
    if messages and messages[0].get("role") == "system":
        return list(messages)
    return [{"role": "system", "content": SISTEMA_PT}] + list(messages)


# --------------------------------------------------------------------------- JSON
def extrair_json(texto: str) -> Any:
    """Primeiro valor JSON (objeto ou lista) do texto; tolera cercas ``` e texto em volta."""
    t = (texto or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "[{":
            try:
                valor, _ = dec.raw_decode(t[i:])
                return valor
            except ValueError:
                continue
    raise ValueError("nenhum JSON encontrado na resposta")


def _validar(bruto: str, adaptador: Optional[TypeAdapter], validar: Optional[Callable[[Any], Any]]) -> Any:
    dados = extrair_json(bruto)
    if adaptador is not None:
        dados = adaptador.validate_python(dados)
    if validar is not None:
        novo = validar(dados)  # levanta ValueError com mensagem para o modelo corrigir
        if novo is not None:
            dados = novo
    return dados


def _msg_erro(e: Exception) -> str:
    if isinstance(e, ValidationError):
        partes = []
        for err in e.errors()[:5]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            partes.append(f"{loc}: {err.get('msg')}")
        return "; ".join(partes)
    return str(e)[:300]


def chat_json(messages: List[dict], schema: Any, finalidade: str, max_tokens: int = 2000,
              timeout_s: float = 0, validar: Optional[Callable[[Any], Any]] = None) -> ResultadoLLM:
    """JSON validado. `schema`: tipo Pydantic/anotação (ou None); `validar(dados)` pode
    levantar ValueError (vira mensagem de correção no retry) ou devolver dados ajustados.
    Um retry por provedor; provedor que falha → próximo. Nunca levanta."""
    adaptador = TypeAdapter(schema) if schema is not None else None
    msgs = _com_sistema(messages)
    timeout_s = timeout_s or getattr(config, "LLM_TIMEOUT_S", 180)
    res = ResultadoLLM(ok=False)
    for nome, fn in _provedores():
        conversa = list(msgs)
        for tentativa in range(2):
            res.tentativas += 1
            try:
                texto, modelo = fn(conversa, max_tokens, timeout_s, finalidade)
            except Exception as e:  # rede/HTTP/vazio: não adianta pedir correção
                res.erros_provedor.append(f"{nome}: {type(e).__name__}: {str(e)[:200]}")
                telemetria.fallback(f"llm.{nome}", f"{type(e).__name__}: {str(e)[:200]}",
                                    finalidade=finalidade)
                break
            try:
                dados = _validar(texto, adaptador, validar)
                return ResultadoLLM(ok=True, dados=dados, texto=texto, motor=nome, modelo=modelo,
                                    tentativas=res.tentativas, erros_provedor=res.erros_provedor)
            except (ValueError, ValidationError) as e:
                msg = _msg_erro(e)
                res.texto = texto
                if tentativa == 0:
                    telemetria.evento("llm_retry", finalidade=finalidade, motor=nome, erro=msg)
                    conversa = conversa + [
                        {"role": "assistant", "content": texto[:4000]},
                        {"role": "user", "content": (
                            f"Sua resposta anterior não está no formato pedido ({msg}). "
                            "Responda de novo, em português, SOMENTE com o JSON no formato pedido, "
                            "sem texto antes ou depois.")}]
                    continue
                res.erros_provedor.append(f"{nome}: JSON inválido após retry: {msg}")
                telemetria.fallback(f"llm.{nome}", f"JSON inválido após retry: {msg}",
                                    finalidade=finalidade)
    res.erro = " | ".join(res.erros_provedor)[:600] or "nenhum provedor LLM disponível"
    return res


def chat_texto(messages: List[dict], finalidade: str, max_tokens: int = 1500,
               timeout_s: float = 0) -> ResultadoLLM:
    """Texto livre com o mesmo roteamento (sem validação). Nunca levanta."""
    msgs = _com_sistema(messages)
    timeout_s = timeout_s or getattr(config, "LLM_TIMEOUT_S", 180)
    res = ResultadoLLM(ok=False)
    for nome, fn in _provedores():
        res.tentativas += 1
        try:
            texto, modelo = fn(msgs, max_tokens, timeout_s, finalidade)
            return ResultadoLLM(ok=True, texto=texto, dados=texto, motor=nome, modelo=modelo,
                                tentativas=res.tentativas, erros_provedor=res.erros_provedor)
        except Exception as e:
            res.erros_provedor.append(f"{nome}: {type(e).__name__}: {str(e)[:200]}")
            telemetria.fallback(f"llm.{nome}", f"{type(e).__name__}: {str(e)[:200]}", finalidade=finalidade)
    res.erro = " | ".join(res.erros_provedor)[:600] or "nenhum provedor LLM disponível"
    return res

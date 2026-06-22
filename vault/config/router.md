# Kee — Router config

The router is `llama3.2:1b` running locally. It classifies every
incoming user message into a tier and routes it to the right LLM:

  - **direct**:         router answers from a template here, no further LLM call (~$0)
  - **simple**:         one tool, terse answer → Ollama (configured local model, free)
  - **conversational**: casual chat, opinion, small talk → GPT-4o-mini ($0.15/$0.60)
  - **medium**:         needs project context, multi-step → Claude Haiku 4.5 ($0.80/$4)
  - **heavy**:          planning, strategy, long-form → Claude Sonnet 4.6 ($3/$15)

If the kill switch fires (>$2/day), all paid tiers downgrade to Ollama
for the rest of the day.

## DIRECT_ANSWERS

Each entry is a regex (case-insensitive) → response template. The router
substitutes `{time}`, `{date}`, `{day}` and `{user}` from the system
context block. First match wins. Add new ones here when you find a
phrase you ask often that doesn't need a real LLM call.

```yaml
- match: '^\s*(hola|hey|buenas|qu[eé] tal|hi|hello)\s*[!.?]?$'
  reply: 'Hola Coco.'

- match: '^\s*(adi[oó]s|chao|bye|hasta luego|nos vemos)\s*[!.?]?$'
  reply: 'Hasta luego.'

- match: '^\s*(gracias|thanks|thank you)\s*[!.?]?$'
  reply: 'A tus órdenes.'

- match: '^\s*(qu[eé] hora es|qu[eé] horas son|la hora|qu[eé] hora|what time|the time)\s*[?.!]?$'
  reply: 'Las {time}.'

- match: '^\s*(qu[eé] d[ií]a es|qu[eé] fecha|d[ií]a de hoy|what day|today.*date)\s*[?.!]?$'
  reply: 'Hoy es {day}, {date}.'

- match: '^\s*(quien eres|qu[ií]en eres|qu[eé] eres|tu nombre|who are you|your name)\s*[?.!]?$'
  reply: 'Soy Kee, tu agente personal soberano.'

- match: '^\s*(ping|test|prueba)\s*[?.!]?$'
  reply: 'Pong.'
```

## TIER_HINTS

Optional: phrases that strongly suggest a particular tier (override
the router's classification). The router prepends these to its prompt
so it's biased correctly.

```yaml
simple:
  - 'cu[aá]ntos correos'
  - 'qu[eé] tengo hoy'
  - 'qu[eé] eventos'
  - 'env[ií]a un email a'
  - 'mi calendario'
  - 'hora actual'

conversational:
  - 'qu[eé] tal'
  - 'c[oó]mo est[aá]s'
  - 'qu[eé] onda'
  - 'h[aá]blame'
  - 'cu[eé]ntame'
  - 'platicame'
  - 'jaja'
  - 'lol'
  - 'chistoso'
  - 'qu[eé] piensas'
  - 'qu[eé] crees'
  - 'qu[eé] opinas'

medium:
  - 'c[oó]mo va'
  - 'estado de'
  - 'qu[eé] hay de'
  - 'busca en'
  - 'compara'
  - 'analiza'
  - 'revisa'
  - 'encuentra el bug'

heavy:
  - 'haz un plan'
  - 'planea'
  - 'estrategia para'
  - 'desglosa'
  - 'explica detalladamente'
  - 'genera un reporte'
  - 'redacta'
  - 'dise[nñ]a'
  - 'arquitectura para'
```
